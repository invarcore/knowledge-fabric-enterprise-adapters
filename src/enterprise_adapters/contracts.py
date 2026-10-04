# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Private adapter contracts for Phase 3."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class AdapterContext:
    """Runtime context shared by adapter implementations."""

    adapter_name: str
    environment: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ReadOnlyResource:
    """Generic resource descriptor for read-only discovery flows."""

    resource_id: str
    name: str
    metadata: dict[str, object] = field(default_factory=dict)


class KnowledgeSourceAdapter(Protocol):
    """Read-only private source adapter contract."""

    def list_resources(self) -> list[ReadOnlyResource]:  # pragma: no cover
        """List discoverable resources."""

    def fetch_resource(self, resource_id: str) -> dict[str, object]:  # pragma: no cover
        """Fetch resource content and metadata."""


class RuntimeMetadataAdapter(Protocol):
    """Read-only runtime discovery contract."""

    def list_runtime_tools(self) -> list[ReadOnlyResource]:  # pragma: no cover
        """List discoverable read-only runtime tools."""

    def list_case_types_or_equivalent(self) -> list[ReadOnlyResource]:  # pragma: no cover
        """List discoverable domain objects or case-like targets."""

    def get_metadata(self, target_id: str) -> dict[str, object]:  # pragma: no cover
        """Return runtime metadata for a target."""


class RuntimeActionAdapter(Protocol):
    """Policy-gated runtime action contract."""

    def prepare_action(self, action: dict[str, object]) -> dict[str, object]:  # pragma: no cover
        """Prepare an action payload without executing it."""

    def execute_action(self, action: dict[str, object]) -> dict[str, object]:  # pragma: no cover
        """Execute an approved action."""
