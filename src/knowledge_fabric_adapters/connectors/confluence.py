# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Confluence Cloud and Server source adapter.

Implements KnowledgeSourceAdapter to ingest Confluence spaces and pages
into Knowledge Fabric without external dependencies (urllib only).
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from knowledge_fabric_adapters.contracts import ReadOnlyResource


class _ConfluenceHTMLToMarkdown(HTMLParser):
    """Converts Confluence storage XHTML into readable clean Markdown."""

    def __init__(self) -> None:
        super().__init__()
        self._output: list[str] = []
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag_stack.append(tag)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._output.append(f"\n\n{'#' * level} ")
        elif tag == "p":
            self._output.append("\n\n")
        elif tag == "li":
            self._output.append("\n- ")
        elif tag == "pre" or tag == "ac:structured-macro":
            self._output.append("\n```\n")

    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self._output.append("\n")
        elif tag == "pre":
            self._output.append("\n```\n")

    def handle_data(self, data: str) -> None:
        cleaned = re.sub(r"\s+", " ", data)
        if cleaned.strip():
            self._output.append(data)

    def get_text(self) -> str:
        raw = "".join(self._output).strip()
        return re.sub(r"\n{3,}", "\n\n", raw)


@dataclass
class ConfluenceSourceAdapter:
    """Read-only adapter for Atlassian Confluence spaces and pages.

    Attributes:
        base_url: e.g. "https://my-company.atlassian.net/wiki"
        space_keys: List of space keys to crawl (e.g. ["ENG", "PROD"])
        email: Atlassian account email
        api_token: Atlassian API token or Bearer token
    """

    base_url: str
    space_keys: list[str]
    email: str = ""
    api_token: str = ""

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    @property
    def source_type(self) -> str:
        return "confluence"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "knowledge-fabric-confluence-adapter/1.0",
        }
        if self.email and self.api_token:
            auth_str = f"{self.email}:{self.api_token}"
            encoded = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        elif self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _request_json(self, path: str, query_params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query_params:
            url = f"{url}?{urlencode(query_params)}"
        req = Request(url, headers=self._headers(), method="GET")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            from knowledge_fabric_adapters.security import sanitize_exception
            raise RuntimeError(f"Confluence request failed: {sanitize_exception(exc)}") from None

    def list_resources(self, since: datetime | None = None) -> list[ReadOnlyResource]:
        """List all pages across configured spaces."""
        resources: list[ReadOnlyResource] = []

        for space_key in self.space_keys:
            start = 0
            limit = 50
            while True:
                params = {
                    "spaceKey": space_key,
                    "expand": "version,metadata.labels",
                    "start": start,
                    "limit": limit,
                    "status": "current",
                }
                try:
                    payload = self._request_json("/rest/api/content", params)
                except Exception:
                    break

                results = payload.get("results", [])
                if not results:
                    break

                for page in results:
                    page_id = str(page.get("id"))
                    title = page.get("title", f"Confluence Page {page_id}")
                    version_info = page.get("version", {})
                    when_str = version_info.get("when", "")

                    labels = [
                        label.get("name")
                        for label in page.get("metadata", {}).get("labels", {}).get("results", [])
                        if label.get("name")
                    ]

                    metadata = {
                        "space_key": space_key,
                        "page_id": page_id,
                        "title": title,
                        "labels": labels,
                        "version": version_info.get("number", 1),
                        "modified_at": when_str,
                        "url": f"{self.base_url}/spaces/{space_key}/pages/{page_id}",
                    }

                    resources.append(
                        ReadOnlyResource(
                            resource_id=page_id,
                            name=title,
                            metadata=metadata,
                        )
                    )

                start += len(results)
                if len(results) < limit:
                    break

        return sorted(resources, key=lambda r: r.resource_id)

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        """Fetch and convert a Confluence page's storage body to clean Markdown."""
        params = {"expand": "body.storage,version,space,metadata.labels"}
        payload = self._request_json(f"/rest/api/content/{quote(resource_id)}", params)

        raw_storage = (
            payload.get("body", {}).get("storage", {}).get("value", "")
        )

        parser = _ConfluenceHTMLToMarkdown()
        parser.feed(raw_storage)
        clean_text = parser.get_text()

        title = payload.get("title", "")
        space_key = payload.get("space", {}).get("key", "")
        full_content = f"# {title}\n\n**Space:** {space_key} | **ID:** {resource_id}\n\n{clean_text}"

        return {
            "resource_id": resource_id,
            "path": f"confluence://{space_key}/{resource_id}",
            "relative_path": f"{space_key}/{resource_id}.md",
            "content": full_content,
            "metadata": {
                "source_type": "confluence",
                "space_key": space_key,
                "page_id": resource_id,
                "title": title,
                "version": payload.get("version", {}).get("number", 1),
                "modified_at": payload.get("version", {}).get("when", ""),
            },
        }
