# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Policy evaluation for private adapter actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha1


class PolicyDecisionType(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


@dataclass(slots=True)
class PolicyDecision:
    """Outcome of a policy evaluation."""

    decision_id: str
    decision: PolicyDecisionType
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class PolicyEvaluator:
    """Simple gatekeeper for private adapter actions."""

    def evaluate(self, action: dict[str, object]) -> PolicyDecision:
        action_name = str(action.get("action_name", "unknown"))
        decision_id = _stable_id("policy", action_name)

        if action.get("blocked") is True:
            return PolicyDecision(
                decision_id=decision_id,
                decision=PolicyDecisionType.DENY,
                reasons=["Action is explicitly blocked."],
                metadata={"action_name": action_name},
            )

        if action.get("write") is True and action.get("approval_id") in {None, ""}:
            return PolicyDecision(
                decision_id=decision_id,
                decision=PolicyDecisionType.REQUIRES_APPROVAL,
                reasons=["Write-capable actions require human approval."],
                metadata={"action_name": action_name},
            )

        if action.get("write") is True and action.get("approval_id"):
            return PolicyDecision(
                decision_id=decision_id,
                decision=PolicyDecisionType.ALLOW,
                reasons=["Approved write-capable action may proceed."],
                metadata={"action_name": action_name},
            )

        return PolicyDecision(
            decision_id=decision_id,
            decision=PolicyDecisionType.ALLOW,
            reasons=["Read-only or simulated action is allowed."],
            metadata={"action_name": action_name},
        )


def _stable_id(prefix: str, seed: str) -> str:
    return f"{prefix}_{sha1(seed.encode('utf-8')).hexdigest()[:12]}"
