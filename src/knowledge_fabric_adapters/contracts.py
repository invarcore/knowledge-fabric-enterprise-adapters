# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Public adapter contracts for the knowledge-fabric ecosystem.

These interfaces define how custom source adapters and runtime adapters
integrate with knowledge-fabric and intent-fabric. Implement these Protocols
in your own package to build community adapters (Slack, ServiceNow, Notion, etc.)

Example adapter implementation:

    from knowledge_fabric_adapters import KnowledgeSourceAdapter, ReadOnlyResource

    class SlackAdapter:
        \"\"\"Fetches messages from a Slack workspace channel.\"\"\"

        def list_resources(self) -> list[ReadOnlyResource]:
            # Return a resource per channel
            ...

        def fetch_resource(self, resource_id: str) -> dict[str, object]:
            # Return the channel messages as content
            ...
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class AdapterContext:
    """Runtime context shared by adapter implementations.

    Attributes:
        adapter_name: Unique name identifying this adapter instance.
        environment: Deployment environment (e.g. "production", "staging").
        metadata: Arbitrary adapter-specific configuration metadata.
    """

    adapter_name: str
    environment: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ReadOnlyResource:
    """Generic resource descriptor returned by KnowledgeSourceAdapter.list_resources().

    Attributes:
        resource_id: Stable identifier passed back to fetch_resource().
        name: Human-readable display name.
        metadata: Arbitrary resource-level metadata (size, modified_at, author, etc.).
    """

    resource_id: str
    name: str
    metadata: dict[str, object] = field(default_factory=dict)


class KnowledgeSourceAdapter(Protocol):
    """Protocol for read-only knowledge source adapters.

    Implement this in your custom adapter to integrate any content source
    (Slack, Notion, ServiceNow, GitHub, Google Drive, etc.) with knowledge-fabric's
    ingestion pipeline.

    Both methods must be implemented. The adapter is always read-only — no writes.
    """

    def list_resources(self) -> list[ReadOnlyResource]:  # pragma: no cover
        """List all discoverable resources from this source.

        Should return a flat list of resources. Pagination, if needed, must be
        handled internally (collect all pages before returning).

        Returns:
            List of ReadOnlyResource instances with stable resource_ids.
        """

    def fetch_resource(self, resource_id: str) -> dict[str, object]:  # pragma: no cover
        """Fetch the full content and metadata for one resource.

        Args:
            resource_id: A resource_id previously returned by list_resources().

        Returns:
            Dictionary with at minimum the keys:
                "resource_id": str
                "content": str       — the text content to be indexed
                "metadata": dict     — arbitrary key/value metadata
        """


class RuntimeMetadataAdapter(Protocol):
    """Protocol for read-only runtime metadata discovery.

    Used by intent-fabric to enumerate available targets (e.g. Jira projects,
    ServiceNow queues, Slack channels) before constructing action plans.
    """

    def list_runtime_tools(self) -> list[ReadOnlyResource]:  # pragma: no cover
        """List discoverable read-only runtime tool capabilities."""

    def list_case_types_or_equivalent(self) -> list[ReadOnlyResource]:  # pragma: no cover
        """List discoverable domain objects or case-like targets."""

    def get_metadata(self, target_id: str) -> dict[str, object]:  # pragma: no cover
        """Return runtime metadata for a specific target."""


class RuntimeActionAdapter(Protocol):
    """Protocol for policy-gated runtime action execution.

    Implementations of this protocol are used by intent-fabric to execute
    approved actions. The two-phase prepare/execute model ensures that no
    action runs without an explicit approval signal from the policy engine.
    """

    def prepare_action(self, action: dict[str, object]) -> dict[str, object]:  # pragma: no cover
        """Validate and prepare an action payload without executing it.

        Should return the prepared payload that will be passed to execute_action()
        after human approval. Use this to validate credentials, resolve IDs, etc.
        """

    def execute_action(self, action: dict[str, object]) -> dict[str, object]:  # pragma: no cover
        """Execute an approved action and return a result payload.

        This method should only be called with an action that has been through
        the policy engine and received explicit human approval.
        """


logger = logging.getLogger("knowledge_fabric_adapters")


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
    """Verify an approval decision's cryptographic HMAC-SHA256 signature in constant time.

    Used by execution adapters to ensure that destructive actions are backed by an
    authentic, non-repudiable human-in-the-loop authorization token.

    Modes:
        - "enforce" (default): strictly requires a valid cryptographic signature.
        - "permissive" / "audit_only": logs a warning if missing or invalid, but returns True
          to avoid hindering local development or test staging setups. Configured via
          FABRIC_APPROVAL_ENFORCEMENT=permissive.
    """
    mode = (enforcement_mode or os.environ.get("FABRIC_APPROVAL_ENFORCEMENT", "enforce")).lower().strip()
    key = secret_key or os.environ.get("FABRIC_SIGNING_KEY", "fabric-insecure-dev-hmac-key-change-in-production")
    sorted_steps = ",".join(sorted(step_ids))
    canonical = f"{approval_id}|{plan_id}|{sorted_steps}|{decision.strip().lower()}|{reviewer.strip()}|{timestamp.strip()}"
    expected = hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    is_match = bool(signature and isinstance(signature, str) and hmac.compare_digest(expected, signature))

    if not is_match and mode in ("permissive", "audit_only", "disabled"):
        logger.warning(
            "Approval signature mismatch or missing for %s (running in %s mode; bypassing block)",
            approval_id,
            mode,
        )
        return True

    return is_match


