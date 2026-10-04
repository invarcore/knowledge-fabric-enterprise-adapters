# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Notion REST API source adapter.

Implements KnowledgeSourceAdapter to ingest Notion databases and pages
into Knowledge Fabric without external dependencies (urllib only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

from knowledge_fabric_adapters.contracts import ReadOnlyResource


def _extract_plain_text(rich_texts: list[dict[str, Any]]) -> str:
    """Extract concatenated plain text from Notion rich_text arrays."""
    return "".join(item.get("plain_text", "") for item in rich_texts)


def _block_to_markdown(block: dict[str, Any]) -> str:
    """Convert a single Notion block object into Markdown."""
    b_type = block.get("type", "")
    data = block.get(b_type, {})

    if b_type == "paragraph":
        return _extract_plain_text(data.get("rich_text", [])) + "\n\n"
    elif b_type == "heading_1":
        return "# " + _extract_plain_text(data.get("rich_text", [])) + "\n\n"
    elif b_type == "heading_2":
        return "## " + _extract_plain_text(data.get("rich_text", [])) + "\n\n"
    elif b_type == "heading_3":
        return "### " + _extract_plain_text(data.get("rich_text", [])) + "\n\n"
    elif b_type == "bulleted_list_item":
        return "- " + _extract_plain_text(data.get("rich_text", [])) + "\n"
    elif b_type == "numbered_list_item":
        return "1. " + _extract_plain_text(data.get("rich_text", [])) + "\n"
    elif b_type == "to_do":
        checked = "x" if data.get("checked") else " "
        return f"- [{checked}] " + _extract_plain_text(data.get("rich_text", [])) + "\n"
    elif b_type == "code":
        lang = data.get("language", "")
        code = _extract_plain_text(data.get("rich_text", []))
        return f"```{lang}\n{code}\n```\n\n"
    elif b_type == "quote":
        return "> " + _extract_plain_text(data.get("rich_text", [])) + "\n\n"
    elif b_type == "callout":
        emoji = data.get("icon", {}).get("emoji", "\u2139\ufe0f")
        return f"> {emoji} " + _extract_plain_text(data.get("rich_text", [])) + "\n\n"
    elif b_type == "divider":
        return "---\n\n"

    return ""


@dataclass
class NotionSourceAdapter:
    """Read-only adapter for Notion databases and pages.

    Attributes:
        api_key: Notion internal integration secret (secret_...)
        database_ids: Optional list of database IDs to crawl
        page_ids: Optional list of root page IDs to crawl
    """

    api_key: str
    database_ids: list[str] | None = None
    page_ids: list[str] | None = None
    notion_version: str = "2022-06-28"

    @property
    def source_type(self) -> str:
        return "notion"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
            "User-Agent": "knowledge-fabric-notion-adapter/1.0",
        }

    def _request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"https://api.notion.com/v1{path}"
        data = json.dumps(payload).encode("utf-8") if payload else None
        req = Request(url, headers=self._headers(), data=data, method=method)
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            from knowledge_fabric_adapters.security import sanitize_exception
            raise RuntimeError(f"Notion request failed: {sanitize_exception(exc)}") from None

    def list_resources(self) -> list[ReadOnlyResource]:
        """List all pages from target databases or workspace search."""
        resources: list[ReadOnlyResource] = []

        if self.database_ids:
            for db_id in self.database_ids:
                has_more = True
                next_cursor = None
                while has_more:
                    body: dict[str, Any] = {"page_size": 100}
                    if next_cursor:
                        body["start_cursor"] = next_cursor
                    try:
                        res = self._request(f"/databases/{db_id}/query", method="POST", payload=body)
                    except Exception:
                        break

                    for page in res.get("results", []):
                        page_id = page["id"]
                        title = self._get_page_title(page)
                        resources.append(
                            ReadOnlyResource(
                                resource_id=page_id,
                                name=title,
                                metadata={
                                    "database_id": db_id,
                                    "page_id": page_id,
                                    "created_time": page.get("created_time"),
                                    "last_edited_time": page.get("last_edited_time"),
                                    "url": page.get("url"),
                                },
                            )
                        )
                    has_more = res.get("has_more", False)
                    next_cursor = res.get("next_cursor")

        if self.page_ids:
            for pid in self.page_ids:
                try:
                    page = self._request(f"/pages/{pid}", method="GET")
                    title = self._get_page_title(page)
                    resources.append(
                        ReadOnlyResource(
                            resource_id=pid,
                            name=title,
                            metadata={
                                "page_id": pid,
                                "created_time": page.get("created_time"),
                                "last_edited_time": page.get("last_edited_time"),
                                "url": page.get("url"),
                            },
                        )
                    )
                except Exception:
                    continue

        return sorted(resources, key=lambda r: r.resource_id)

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        """Fetch page metadata and block children, assembling into clean Markdown."""
        page = self._request(f"/pages/{resource_id}", method="GET")
        title = self._get_page_title(page)

        # Retrieve blocks
        blocks: list[str] = []
        has_more = True
        next_cursor = None
        while has_more:
            url_path = f"/blocks/{resource_id}/children?page_size=100"
            if next_cursor:
                url_path += f"&start_cursor={next_cursor}"
            res = self._request(url_path, method="GET")
            for block in res.get("results", []):
                blocks.append(_block_to_markdown(block))
            has_more = res.get("has_more", False)
            next_cursor = res.get("next_cursor")

        content = f"# {title}\n\n" + "".join(blocks).strip()

        return {
            "resource_id": resource_id,
            "path": f"notion://{resource_id}",
            "relative_path": f"{resource_id}.md",
            "content": content,
            "metadata": {
                "source_type": "notion",
                "page_id": resource_id,
                "title": title,
                "url": page.get("url", ""),
                "last_edited_time": page.get("last_edited_time", ""),
            },
        }

    def _get_page_title(self, page: dict[str, Any]) -> str:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                return _extract_plain_text(prop.get("title", [])) or "Untitled Notion Page"
        return "Untitled Notion Page"
