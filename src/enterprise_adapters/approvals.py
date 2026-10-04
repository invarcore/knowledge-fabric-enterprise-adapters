# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Approval request shapes for private adapter actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha1

from enterprise_adapters.policy import PolicyDecision


@dataclass(slots=True)
class ApprovalRequest:
    """Human approval request for a private action."""

    approval_id: str
    action_name: str
    requested_by: str
    summary: str
    policy_reasons: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class ApprovalPackageGenerator:
    """Builds approval requests from policy outcomes."""

    def create(self, *, action: dict[str, object], policy_decision: PolicyDecision, requested_by: str) -> ApprovalRequest:
        action_name = str(action.get("action_name", "unknown"))
        approval_id = _stable_id("approval", f"{action_name}:{requested_by}")
        summary = f"Approval required for action '{action_name}'."
        return ApprovalRequest(
            approval_id=approval_id,
            action_name=action_name,
            requested_by=requested_by,
            summary=summary,
            policy_reasons=policy_decision.reasons,
            metadata={"decision": policy_decision.decision.value},
        )


def _stable_id(prefix: str, seed: str) -> str:
    return f"{prefix}_{sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def canonical_approval_payload(
    *,
    approval_id: str,
    plan_id: str,
    step_ids: list[str],
    decision: str,
    reviewer: str,
    timestamp: str,
) -> bytes:
    """Create deterministic canonical byte string representing an approval decision."""
    sorted_steps = ",".join(sorted(step_ids))
    canonical_str = f"{approval_id}|{plan_id}|{sorted_steps}|{decision.strip().lower()}|{reviewer.strip()}|{timestamp.strip()}"
    return canonical_str.encode("utf-8")


def compute_approval_signature(
    *,
    approval_id: str,
    plan_id: str,
    step_ids: list[str],
    decision: str,
    reviewer: str,
    timestamp: str,
    secret_key: str | None = None,
) -> str:
    """Compute HMAC-SHA256 hex signature over canonical approval decision."""
    import hashlib
    import hmac
    import os

    key = secret_key or os.environ.get("FABRIC_SIGNING_KEY", "fabric-insecure-dev-hmac-key-change-in-production")
    payload = canonical_approval_payload(
        approval_id=approval_id,
        plan_id=plan_id,
        step_ids=step_ids,
        decision=decision,
        reviewer=reviewer,
        timestamp=timestamp,
    )
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_approval_signature(
    *,
    approval_id: str,
    plan_id: str,
    step_ids: list[str],
    decision: str,
    reviewer: str,
    timestamp: str,
    signature: str,
    secret_key: str | None = None,
    enforcement_mode: str | None = None,
) -> bool:
    """Verify approval decision cryptographic HMAC-SHA256 signature in constant time."""
    from knowledge_fabric_adapters.contracts import verify_approval_signature as _kfa_verify
    return _kfa_verify(
        approval_id=approval_id,
        plan_id=plan_id,
        step_ids=step_ids,
        decision=decision,
        reviewer=reviewer,
        timestamp=timestamp,
        signature=signature,
        secret_key=secret_key,
        enforcement_mode=enforcement_mode,
    )

