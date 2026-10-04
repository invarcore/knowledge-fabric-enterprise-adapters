# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Simulation-only action preparation for private runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha1

from enterprise_adapters.policy import PolicyDecision


@dataclass(slots=True)
class PreparedAction:
    """Validated action payload that is ready for simulation."""

    prepared_action_id: str
    action_name: str
    payload: dict[str, object]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SimulationResult:
    """Result of a dry-run style action simulation."""

    simulation_id: str
    action_name: str
    status: str
    payload: dict[str, object]
    logs: list[str] = field(default_factory=list)
    no_external_side_effects: bool = True


class SimulationOnlyRuntimeActionAdapter:
    """Prepares and simulates actions without performing real writes."""

    def prepare_action(self, action: dict[str, object]) -> dict[str, object]:
        action_name = _require_action_name(action)
        payload = dict(action)
        payload["simulated"] = True
        payload["validated"] = True
        return {
            "prepared_action_id": _stable_id("prepared", action_name),
            "action_name": action_name,
            "payload": payload,
            "metadata": {
                "write": bool(action.get("write", False)),
                "requires_approval": bool(action.get("approval_id") in {None, ""} and action.get("write") is True),
            },
        }

    def execute_action(self, action: dict[str, object]) -> dict[str, object]:
        prepared = self.prepare_action(action)
        result = SimulationResult(
            simulation_id=_stable_id("simulation", str(prepared["prepared_action_id"])),
            action_name=str(prepared["action_name"]),
            status="simulated",
            payload=prepared["payload"],
            logs=["Prepared action only; no external side effects were produced."],
            no_external_side_effects=True,
        )
        return {
            "simulation_id": result.simulation_id,
            "action_name": result.action_name,
            "status": result.status,
            "payload": result.payload,
            "logs": result.logs,
            "no_external_side_effects": result.no_external_side_effects,
        }

    def simulate_with_policy(self, action: dict[str, object], policy_decision: PolicyDecision) -> dict[str, object]:
        prepared = self.prepare_action(action)
        status = "simulated"
        logs = ["Action prepared for simulation."]
        if policy_decision.decision.value == "deny":
            status = "blocked"
            logs.append("Policy denied the action.")
        elif policy_decision.decision.value == "requires_approval":
            status = "pending_approval"
            logs.append("Policy requires approval before execution.")
        return {
            "simulation_id": _stable_id("simulation", str(prepared["prepared_action_id"])),
            "action_name": prepared["action_name"],
            "status": status,
            "prepared_action": prepared,
            "logs": logs,
            "no_external_side_effects": True,
        }


def _require_action_name(action: dict[str, object]) -> str:
    action_name = str(action.get("action_name", "")).strip()
    if not action_name:
        raise ValueError("action_name is required")
    return action_name


def _stable_id(prefix: str, seed: str) -> str:
    return f"{prefix}_{sha1(seed.encode('utf-8')).hexdigest()[:12]}"
