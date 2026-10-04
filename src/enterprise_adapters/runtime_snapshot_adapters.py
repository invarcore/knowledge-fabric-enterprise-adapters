# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Snapshot-backed read-only runtime discovery adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from enterprise_adapters.contracts import ReadOnlyResource


@dataclass(slots=True)
class RuntimeSnapshotDiscoveryAdapter:
    """Read-only runtime discovery from a local snapshot."""

    snapshot_name: str
    runtime_tools: list[ReadOnlyResource] = field(default_factory=list)
    case_types_or_equivalent: list[ReadOnlyResource] = field(default_factory=list)
    metadata_by_target_id: dict[str, dict[str, object]] = field(default_factory=dict)

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
    def from_file(cls, snapshot_path: str | Path) -> "RuntimeSnapshotDiscoveryAdapter":
        path = Path(snapshot_path).expanduser().resolve()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_mapping(raw, snapshot_name=path.stem)

    @classmethod
    def from_mapping(
        cls,
        payload: dict[str, Any],
        *,
        snapshot_name: str = "snapshot",
    ) -> "RuntimeSnapshotDiscoveryAdapter":
        return cls(
            snapshot_name=snapshot_name,
            runtime_tools=_load_resources(payload.get("runtime_tools", [])),
            case_types_or_equivalent=_load_resources(payload.get("case_types_or_equivalent", [])),
            metadata_by_target_id={
                str(key): dict(value) for key, value in dict(payload.get("metadata_by_target_id", {})).items()
            },
        )


def _load_resources(items: list[dict[str, Any]]) -> list[ReadOnlyResource]:
    resources: list[ReadOnlyResource] = []
    for item in items:
        resources.append(
            ReadOnlyResource(
                resource_id=str(item["resource_id"]),
                name=str(item["name"]),
                metadata=dict(item.get("metadata", {})),
            )
        )
    return resources
