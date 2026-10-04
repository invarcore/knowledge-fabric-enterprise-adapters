# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Read-only runtime discovery adapters for private systems."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Iterable

from enterprise_adapters.contracts import ReadOnlyResource


@dataclass(slots=True)
class RuntimeAuthConfig:
    """Environment-based runtime connection settings."""

    base_url: str = ""
    auth_token: str = ""
    environment: str = "local"
    metadata: dict[str, object] = field(default_factory=dict)


def load_runtime_auth_config() -> RuntimeAuthConfig:
    """Load runtime settings from environment variables."""
    return RuntimeAuthConfig(
        base_url=os.environ.get("RUNTIME_API_BASE_URL", ""),
        auth_token=os.environ.get("RUNTIME_AUTH_TOKEN", ""),
        environment=os.environ.get("RUNTIME_ENV", "local"),
    )


@dataclass(slots=True)
class ReadOnlyRuntimeDiscoveryAdapter:
    """Generic read-only runtime discovery adapter."""

    runtime_tools: list[ReadOnlyResource]
    case_types_or_equivalent: list[ReadOnlyResource]
    metadata_by_target_id: dict[str, dict[str, object]]

    def list_runtime_tools(self) -> list[ReadOnlyResource]:
        return list(self.runtime_tools)

    def list_case_types_or_equivalent(self) -> list[ReadOnlyResource]:
        return list(self.case_types_or_equivalent)

    def get_metadata(self, target_id: str) -> dict[str, object]:
        try:
            return dict(self.metadata_by_target_id[target_id])
        except KeyError as exc:
            raise KeyError(f"Unknown runtime target: {target_id}") from exc

    @classmethod
    def from_discovery(
        cls,
        *,
        runtime_tools: Iterable[ReadOnlyResource],
        case_types_or_equivalent: Iterable[ReadOnlyResource],
        metadata_by_target_id: dict[str, dict[str, object]] | None = None,
    ) -> "ReadOnlyRuntimeDiscoveryAdapter":
        return cls(
            runtime_tools=list(runtime_tools),
            case_types_or_equivalent=list(case_types_or_equivalent),
            metadata_by_target_id=metadata_by_target_id or {},
        )
