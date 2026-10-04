# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Google Drive API source adapter.

Implements KnowledgeSourceAdapter to ingest Google Docs and files from Google Drive
folders into Knowledge Fabric without external dependencies (urllib only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from knowledge_fabric_adapters.contracts import ReadOnlyResource


@dataclass(slots=True)
class GoogleDriveSourceAdapter:
    """Read-only adapter for Google Drive folders and Google Docs.

    Attributes:
        access_token: Google OAuth2 or Service Account Bearer token
        folder_id: Target folder ID to crawl (or 'root')
        include_shared_drives: Whether to support Google Workspace Shared Drives
    """

    access_token: str
    folder_id: str = "root"
    include_shared_drives: bool = True

    @property
    def source_type(self) -> str:
        return "google_drive"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "knowledge-fabric-gdrive-adapter/1.0",
        }

    def _request_json(self, url: str) -> dict[str, Any]:
        req = Request(url, headers=self._headers(), method="GET")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            from knowledge_fabric_adapters.security import sanitize_exception
            raise RuntimeError(f"Google Drive request failed: {sanitize_exception(exc)}") from None

    def list_resources(self) -> list[ReadOnlyResource]:
        """List all supported files within target Google Drive folder."""
        resources: list[ReadOnlyResource] = []
        page_token = None

        query = f"'{self.folder_id}' in parents and trashed = false"

        while True:
            params = {
                "q": query,
                "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                "pageSize": 100,
                "supportsAllDrives": str(self.include_shared_drives).lower(),
                "includeItemsFromAllDrives": str(self.include_shared_drives).lower(),
            }
            if page_token:
                params["pageToken"] = page_token

            url = f"https://www.googleapis.com/drive/v3/files?{urlencode(params)}"
            try:
                payload = self._request_json(url)
            except Exception:
                break

            for file in payload.get("files", []):
                mime = file.get("mimeType", "")
                if mime == "application/vnd.google-apps.folder":
                    # Subfolders can be traversed if needed; skipped for flat folder list
                    continue

                file_id = file["id"]
                name = file.get("name", file_id)

                resources.append(
                    ReadOnlyResource(
                        resource_id=file_id,
                        name=name,
                        metadata={
                            "file_id": file_id,
                            "name": name,
                            "mime_type": mime,
                            "modified_time": file.get("modifiedTime", ""),
                            "size": file.get("size", 0),
                        },
                    )
                )

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return sorted(resources, key=lambda r: r.resource_id)

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        """Fetch metadata and content for a Google Drive file.

        Google Docs are exported to plain text via Drive's export endpoint.
        """
        # 1. Get metadata
        meta_url = (
            f"https://www.googleapis.com/drive/v3/files/{quote(resource_id)}"
            f"?fields=id,name,mimeType,modifiedTime&supportsAllDrives={str(self.include_shared_drives).lower()}"
        )
        meta = self._request_json(meta_url)
        mime = meta.get("mimeType", "")
        name = meta.get("name", resource_id)

        # 2. Get content
        if mime == "application/vnd.google-apps.document":
            # Export Google Doc as text/plain
            content_url = f"https://www.googleapis.com/drive/v3/files/{quote(resource_id)}/export?mimeType=text/plain"
        else:
            # Download file binary/text
            content_url = f"https://www.googleapis.com/drive/v3/files/{quote(resource_id)}?alt=media"

        req = Request(content_url, headers=self._headers(), method="GET")
        with urlopen(req, timeout=30) as resp:
            raw_bytes = resp.read()
            text_content = raw_bytes.decode("utf-8", errors="replace")

        full_content = f"# {name}\n\n**Google Drive ID:** {resource_id}\n\n{text_content}"

        return {
            "resource_id": resource_id,
            "path": f"gdrive://{resource_id}",
            "relative_path": f"{name}",
            "content": full_content,
            "metadata": {
                "source_type": "google_drive",
                "file_id": resource_id,
                "title": name,
                "mime_type": mime,
                "modified_at": meta.get("modifiedTime", ""),
            },
        }
