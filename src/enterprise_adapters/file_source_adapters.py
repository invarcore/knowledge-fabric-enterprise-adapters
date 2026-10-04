# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Read-only approved file-store source adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from enterprise_adapters.content_sanitizer import sanitize_document_content
from enterprise_adapters.contracts import ReadOnlyResource

_SUPPORTED_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".go",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".csv",
    ".html",
    ".htm",
    ".sh",
}


@dataclass(slots=True)
class ApprovedFileStoreSourceAdapter:
    """Read-only adapter for approved file-store content."""

    source_root: Path
    allowed_roots: list[Path]

    def __post_init__(self) -> None:
        self.source_root = self.source_root.expanduser().resolve()
        self.allowed_roots = [path.expanduser().resolve() for path in self.allowed_roots]
        if not self.allowed_roots:
            raise ValueError("allowed_roots must not be empty")

    def list_resources(self) -> list[ReadOnlyResource]:
        resources: list[ReadOnlyResource] = []
        for root in self._active_roots():
            for file_path in sorted(root.rglob("*")):
                if file_path.is_file() and file_path.suffix.lower() in _SUPPORTED_EXTENSIONS:
                    resources.append(self._resource_from_path(file_path))
        return resources

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        file_path = self._resolve_resource_id(resource_id)
        if not self._is_allowed_path(file_path):
            raise ValueError("resource_id is not under an approved allowlisted root")
        if file_path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {file_path.suffix}")

        stat = file_path.stat()
        content = sanitize_document_content(_normalize_text(file_path.read_bytes().decode("utf-8")))
        return {
            "resource_id": resource_id,
            "path": str(file_path),
            "relative_path": file_path.relative_to(self.source_root).as_posix(),
            "content": content,
            "metadata": {
                "name": file_path.name,
                "extension": file_path.suffix.lower(),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "source_root": str(self.source_root),
            },
        }

    def _active_roots(self) -> Iterable[Path]:
        for root in self.allowed_roots:
            if self._is_under(root, self.source_root):
                yield root

    def _resolve_resource_id(self, resource_id: str) -> Path:
        candidate = (self.source_root / resource_id).expanduser().resolve()
        if not self._is_under(candidate, self.source_root):
            raise ValueError("resource_id escapes the approved source root")
        if not candidate.exists():
            raise FileNotFoundError(f"Resource not found: {resource_id}")
        return candidate

    def _is_allowed_path(self, candidate: Path) -> bool:
        return any(self._is_under(candidate, root) for root in self.allowed_roots)

    @staticmethod
    def _is_under(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def _resource_from_path(self, file_path: Path) -> ReadOnlyResource:
        stat = file_path.stat()
        return ReadOnlyResource(
            resource_id=file_path.relative_to(self.source_root).as_posix(),
            name=file_path.stem,
            metadata={
                "path": str(file_path),
                "relative_path": file_path.relative_to(self.source_root).as_posix(),
                "extension": file_path.suffix.lower(),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            },
        )


def _normalize_text(text: str) -> str:
    text = text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized = "\n".join(lines).strip()
    return normalized + ("\n" if normalized else "")

