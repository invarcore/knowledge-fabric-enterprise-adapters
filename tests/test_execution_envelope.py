"""Unit tests for fail-closed ExecutionEnvelope and readback verification.

Tests the tri-state execution contracts:
- CONFIRMED: mutation landed and post-mutation readback verified.
- UNCERTAIN: mutation dispatched but readback failed/timed out/mismatched.
  Caller is explicitly blocked from blind retries; correlation_id provided.
- FAILED: mutation failed to dispatch; safe to retry.
"""

from __future__ import annotations

import pytest

from enterprise_adapters.execution import (
    ExecutionEnvelope,
    ExecutionStatus,
    dispatch_with_readback,
)


def test_envelope_confirmed_flow() -> None:
    """Readback successfully matches expected state resulting in CONFIRMED."""
    state = {"status": "pending"}

    def dispatch() -> dict[str, str]:
        state["status"] = "deployed"
        return {"result": "success", "deployment_id": "dep-123"}

    def readback() -> dict[str, str]:
        return {"status": "deployed"}

    envelope = dispatch_with_readback(
        action_name="k8s.apply_manifest",
        approval_id="appr-test-1",
        dispatch_fn=dispatch,
        readback_fn=readback,
        expected_state={"status": "deployed"},
    )

    assert envelope.status == ExecutionStatus.CONFIRMED
    assert envelope.is_confirmed is True
    assert envelope.is_safe_to_retry is False
    assert envelope.requires_investigation is False
    assert envelope.action_name == "k8s.apply_manifest"
    assert envelope.approval_id == "appr-test-1"
    assert envelope.readback_state == {"status": "deployed"}
    assert envelope.error is None
    assert any("CONFIRMED" in log for log in envelope.logs)


def test_envelope_uncertain_on_readback_exception() -> None:
    """When readback raises an exception, envelope must be UNCERTAIN (fail-closed)."""
    dispatched = False

    def dispatch() -> dict[str, str]:
        nonlocal dispatched
        dispatched = True
        return {"job_id": "job-999"}

    def readback() -> dict[str, str]:
        raise ConnectionResetError("Cluster control-plane temporarily unreachable")

    envelope = dispatch_with_readback(
        action_name="cloud.provision_vm",
        approval_id="appr-test-2",
        dispatch_fn=dispatch,
        readback_fn=readback,
        expected_state={"provisioned": True},
    )

    assert dispatched is True
    assert envelope.status == ExecutionStatus.UNCERTAIN
    assert envelope.is_confirmed is False
    assert envelope.is_safe_to_retry is False  # Blind retry blocked!
    assert envelope.requires_investigation is True
    assert "ConnectionResetError" in (envelope.error or "")
    assert envelope.correlation_id.startswith("corr-")
    assert any("UNCERTAIN" in log for log in envelope.logs)


def test_envelope_uncertain_on_readback_none() -> None:
    """When readback returns None (timeout or unavailable), envelope is UNCERTAIN."""
    def dispatch() -> dict[str, str]:
        return {"task": "started"}

    def readback() -> None:
        return None

    envelope = dispatch_with_readback(
        action_name="db.migrate",
        approval_id="appr-test-3",
        dispatch_fn=dispatch,
        readback_fn=readback,
        expected_state={"schema_version": 42},
    )

    assert envelope.status == ExecutionStatus.UNCERTAIN
    assert envelope.is_safe_to_retry is False
    assert envelope.requires_investigation is True
    assert "None" in (envelope.error or "")


def test_envelope_uncertain_on_state_mismatch() -> None:
    """When readback state contradicts expected state, envelope is UNCERTAIN."""
    def dispatch() -> dict[str, str]:
        return {"status": "ok"}

    def readback() -> dict[str, object]:
        return {
            "replicas": 1,  # expected was 3
            "version": "v1.2",
        }

    envelope = dispatch_with_readback(
        action_name="scale_service",
        approval_id="appr-test-4",
        dispatch_fn=dispatch,
        readback_fn=readback,
        expected_state={"replicas": 3, "version": "v1.2"},
    )

    assert envelope.status == ExecutionStatus.UNCERTAIN
    assert envelope.is_safe_to_retry is False
    assert envelope.requires_investigation is True
    assert "replicas" in envelope.mismatch_fields
    assert "version" not in envelope.mismatch_fields
    assert any("Mismatch" in log for log in envelope.logs)


def test_envelope_failed_on_dispatch_exception() -> None:
    """When dispatch throws before mutating, envelope is FAILED and safe to retry."""
    def dispatch() -> dict[str, str]:
        raise PermissionError("Access denied: missing role editor")

    def readback() -> dict[str, str]:
        return {}

    envelope = dispatch_with_readback(
        action_name="vault.write_secret",
        approval_id="appr-test-5",
        dispatch_fn=dispatch,
        readback_fn=readback,
        expected_state={"exists": True},
    )

    assert envelope.status == ExecutionStatus.FAILED
    assert envelope.is_safe_to_retry is True
    assert envelope.is_confirmed is False
    assert envelope.requires_investigation is False
    assert "PermissionError" in (envelope.error or "")
    assert envelope.readback_state is None
