"""Approved runtime execution for private adapters.

Includes fail-closed ExecutionEnvelope with tri-state verification
(CONFIRMED, UNCERTAIN, FAILED) and live readback support.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha1
from typing import Any

import os

from enterprise_adapters.approvals import verify_approval_signature
from enterprise_adapters.audit import InMemoryAuditTrail
from enterprise_adapters.policy import PolicyDecision, PolicyDecisionType


@dataclass(slots=True)
class ExecutionReceipt:
    """Outcome record for an approved runtime execution."""

    execution_id: str
    action_name: str
    status: str
    approval_id: str
    logs: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class ExecutionStatus(Enum):
    """Tri-state outcome for fail-closed execution verification.

    - CONFIRMED: Post-mutation readback verified the expected state change.
    - UNCERTAIN: Execution dispatched but readback timed out or returned
      an inconclusive result. The caller MUST NOT blindly retry — use the
      correlation_id to investigate before re-attempting.
    - FAILED: Execution was rejected or raised an error. Safe to retry
      with a corrected payload.
    """

    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


@dataclass(slots=True)
class ExecutionEnvelope:
    """Fail-closed execution envelope with live readback verification.

    Wraps the result of a dispatched mutation with a tri-state verification
    outcome. The envelope guarantees that callers can never silently swallow
    an ambiguous execution result:

    - CONFIRMED  → readback proved the mutation landed.
    - UNCERTAIN  → mutation may or may not have landed; correlation_id
                   provided for manual investigation. Blind retries are
                   explicitly blocked.
    - FAILED     → mutation definitively did not land; safe to retry.
    """

    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    correlation_id: str = field(default_factory=lambda: f"corr-{uuid.uuid4().hex[:16]}")
    status: ExecutionStatus = ExecutionStatus.FAILED
    action_name: str = ""
    approval_id: str = ""
    readback_state: dict[str, Any] | None = None
    expected_state: dict[str, Any] | None = None
    mismatch_fields: list[str] = field(default_factory=list)
    error: str | None = None
    logs: list[str] = field(default_factory=list)

    @property
    def is_safe_to_retry(self) -> bool:
        """Only FAILED envelopes are safe to retry. UNCERTAIN must investigate first."""
        return self.status == ExecutionStatus.FAILED

    @property
    def is_confirmed(self) -> bool:
        return self.status == ExecutionStatus.CONFIRMED

    @property
    def requires_investigation(self) -> bool:
        """UNCERTAIN envelopes require human/automated investigation before retry."""
        return self.status == ExecutionStatus.UNCERTAIN


def dispatch_with_readback(
    action_name: str,
    approval_id: str,
    dispatch_fn: Callable[[], dict[str, Any]],
    readback_fn: Callable[[], dict[str, Any] | None],
    expected_state: dict[str, Any],
) -> ExecutionEnvelope:
    """Execute a mutation and verify via post-mutation readback.

    Args:
        action_name: Human-readable name of the action being dispatched.
        approval_id: Approval token ID authorizing this mutation.
        dispatch_fn: Callable that performs the mutation. Returns a result dict.
        readback_fn: Callable that queries post-mutation state. Returns state
            dict or None if readback is unavailable/timed out.
        expected_state: Dict of key-value pairs that readback_fn should return
            if the mutation landed successfully.

    Returns:
        ExecutionEnvelope with verified tri-state outcome.
    """
    envelope = ExecutionEnvelope(
        action_name=action_name,
        approval_id=approval_id,
        expected_state=expected_state,
    )

    # Phase 1: Dispatch the mutation
    try:
        result = dispatch_fn()
        envelope.logs.append(f"Dispatch succeeded: {action_name}")
        if isinstance(result, dict):
            envelope.logs.extend(
                f"  {k}: {v}" for k, v in list(result.items())[:5]
            )
    except Exception as exc:
        envelope.status = ExecutionStatus.FAILED
        envelope.error = f"Dispatch error: {type(exc).__name__}: {exc}"
        envelope.logs.append(envelope.error)
        return envelope

    # Phase 2: Live readback verification
    try:
        actual_state = readback_fn()
    except Exception as exc:
        # Readback failed — mutation MAY have landed but we can't verify
        envelope.status = ExecutionStatus.UNCERTAIN
        envelope.error = f"Readback error: {type(exc).__name__}: {exc}"
        envelope.logs.append(
            f"UNCERTAIN: Readback failed after dispatch. "
            f"Correlation ID: {envelope.correlation_id}"
        )
        return envelope

    if actual_state is None:
        # Readback returned nothing — timeout or unavailable
        envelope.status = ExecutionStatus.UNCERTAIN
        envelope.error = "Readback returned None (timeout or unavailable)"
        envelope.logs.append(
            f"UNCERTAIN: Readback unavailable. "
            f"Correlation ID: {envelope.correlation_id}"
        )
        return envelope

    envelope.readback_state = actual_state

    # Phase 3: Compare readback against expected state
    mismatches: list[str] = []
    for key, expected_value in expected_state.items():
        actual_value = actual_state.get(key)
        if actual_value != expected_value:
            mismatches.append(
                f"{key}: expected={expected_value!r}, actual={actual_value!r}"
            )

    if mismatches:
        envelope.status = ExecutionStatus.UNCERTAIN
        envelope.mismatch_fields = [m.split(":")[0] for m in mismatches]
        envelope.error = f"Readback mismatch on {len(mismatches)} field(s)"
        envelope.logs.append(
            f"UNCERTAIN: State mismatch after dispatch. "
            f"Correlation ID: {envelope.correlation_id}"
        )
        for m in mismatches:
            envelope.logs.append(f"  Mismatch: {m}")
        return envelope

    # All readback fields match — confirmed!
    envelope.status = ExecutionStatus.CONFIRMED
    envelope.logs.append(
        f"CONFIRMED: All {len(expected_state)} expected fields verified via readback."
    )
    return envelope


class ApprovedRuntimeActionAdapter:
    """Executes approved actions only after policy and approval checks."""

    def __init__(self, audit_trail: InMemoryAuditTrail | None = None) -> None:
        self._audit_trail = audit_trail or InMemoryAuditTrail()

    def _verify_approval_signature(self, action: dict[str, object], approval_id: str) -> None:
        """Verify cryptographic HMAC signature for mutative writes or when token provided."""
        is_write = bool(action.get("write", False))
        has_token = "signature" in action or "approval_token" in action

        if not is_write and not has_token:
            return

        token_dict = action.get("approval_token") if isinstance(action.get("approval_token"), dict) else {}
        signature = str(action.get("signature") or token_dict.get("signature") or "").strip()
        plan_id = str(action.get("plan_id") or token_dict.get("plan_id") or "").strip()
        raw_step_ids = action.get("step_ids") or token_dict.get("step_ids") or [str(action.get("action_name", ""))]
        if isinstance(raw_step_ids, str):
            step_ids = [raw_step_ids]
        else:
            step_ids = [str(s) for s in raw_step_ids]
        decision = str(action.get("decision") or token_dict.get("decision") or "approved").strip()
        reviewer = str(action.get("reviewer") or token_dict.get("reviewer") or "system").strip()
        timestamp = str(action.get("timestamp") or token_dict.get("timestamp") or "").strip()
        secret_key = str(action.get("secret_key")) if action.get("secret_key") else None

        if not signature:
            mode = os.environ.get("FABRIC_APPROVAL_ENFORCEMENT", "enforce").lower().strip()
            if mode in ("permissive", "audit_only", "disabled"):
                self._audit_trail.record(
                    "approval.signature_missing_permissive",
                    approval_id=approval_id,
                    action_name=str(action.get("action_name", "")),
                )
                return
            raise PermissionError(
                f"Approved mutative execution requires a cryptographic approval signature for '{approval_id}'."
            )

        valid = verify_approval_signature(
            approval_id=approval_id,
            plan_id=plan_id,
            step_ids=step_ids,
            decision=decision,
            reviewer=reviewer,
            timestamp=timestamp,
            signature=signature,
            secret_key=secret_key,
        )
        if not valid:
            self._audit_trail.record(
                "approval.verification_failed",
                approval_id=approval_id,
                action_name=str(action.get("action_name", "")),
            )
            raise PermissionError(
                f"Cryptographic approval verification failed for '{approval_id}': invalid or tampered signature."
            )

        self._audit_trail.record(
            "approval.verified",
            approval_id=approval_id,
            action_name=str(action.get("action_name", "")),
            reviewer=reviewer,
        )

    def prepare_action(self, action: dict[str, object]) -> dict[str, object]:
        action_name = _require_action_name(action)
        prepared = dict(action)
        prepared["prepared"] = True
        prepared["write_enabled"] = True
        return {
            "prepared_action_id": _stable_id("prepared", action_name),
            "action_name": action_name,
            "payload": prepared,
            "metadata": {"approval_id": str(action.get("approval_id", ""))},
        }

    def execute_action(self, action: dict[str, object]) -> dict[str, object]:
        policy_decision = _require_policy_decision(action)
        if policy_decision.decision is not PolicyDecisionType.ALLOW:
            raise PermissionError("Approved execution requires an allow policy decision.")

        approval_id = str(action.get("approval_id", "")).strip()
        if not approval_id:
            raise PermissionError("Approved execution requires an approval_id.")

        self._verify_approval_signature(action, approval_id)

        prepared = self.prepare_action(action)

        receipt = ExecutionReceipt(
            execution_id=_stable_id("execution", str(prepared["prepared_action_id"])),
            action_name=str(prepared["action_name"]),
            status="executed",
            approval_id=approval_id,
            logs=["Approved execution completed in the private adapter layer."],
            metadata={"policy_decision": policy_decision.decision.value},
        )
        self._audit_trail.record(
            "execution.completed",
            execution_id=receipt.execution_id,
            action_name=receipt.action_name,
            approval_id=receipt.approval_id,
            policy_decision=policy_decision.decision.value,
        )
        return {
            "execution_id": receipt.execution_id,
            "action_name": receipt.action_name,
            "status": receipt.status,
            "approval_id": receipt.approval_id,
            "logs": receipt.logs,
            "metadata": receipt.metadata,
        }

    def audit_events(self) -> list[object]:
        return self._audit_trail.events()


def _require_action_name(action: dict[str, object]) -> str:
    action_name = str(action.get("action_name", "")).strip()
    if not action_name:
        raise ValueError("action_name is required")
    return action_name


def _require_policy_decision(action: dict[str, object]) -> PolicyDecision:
    raw = action.get("policy_decision")
    if not isinstance(raw, PolicyDecision):
        raise PermissionError("Approved execution requires a policy_decision.")
    return raw


def _stable_id(prefix: str, seed: str) -> str:
    return f"{prefix}_{sha1(seed.encode('utf-8')).hexdigest()[:12]}"
