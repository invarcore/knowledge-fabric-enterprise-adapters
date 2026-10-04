# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Jira Cloud and Server source adapter.

Implements KnowledgeSourceAdapter to ingest Jira issues, post-mortems,
and incident resolution records into Knowledge Fabric without external dependencies.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from knowledge_fabric_adapters.contracts import ReadOnlyResource


@dataclass
class JiraSourceAdapter:
    """Read-only adapter for Jira issues, incidents, and decision records.

    Attributes:
        base_url: e.g. "https://my-company.atlassian.net"
        jql: JQL query string to select issues (default: "statusCategory = Done ORDER BY updated DESC")
        email: Atlassian account email
        api_token: Atlassian API token or Bearer token
    """

    base_url: str
    jql: str = "statusCategory = Done ORDER BY updated DESC"
    email: str = ""
    api_token: str = ""

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    @property
    def source_type(self) -> str:
        return "jira"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "knowledge-fabric-jira-adapter/1.0",
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
            raise RuntimeError(f"Jira request failed: {sanitize_exception(exc)}") from None

    def list_resources(self) -> list[ReadOnlyResource]:
        """List all issues matching the configured JQL query."""
        resources: list[ReadOnlyResource] = []
        start_at = 0
        max_results = 50

        while True:
            params = {
                "jql": self.jql,
                "fields": "summary,status,updated,created,priority,issuetype,project",
                "startAt": start_at,
                "maxResults": max_results,
            }
            try:
                payload = self._request_json("/rest/api/3/search", params)
            except Exception:
                # Fallback to API v2 if v3 is not supported on Server/Data Center
                try:
                    payload = self._request_json("/rest/api/2/search", params)
                except Exception:
                    break

            issues = payload.get("issues", [])
            if not issues:
                break

            for issue in issues:
                issue_key = issue["key"]
                fields = issue.get("fields", {})
                summary = fields.get("summary", issue_key)
                status_name = fields.get("status", {}).get("name", "")
                priority = fields.get("priority", {}).get("name", "")
                project_key = fields.get("project", {}).get("key", "")

                resources.append(
                    ReadOnlyResource(
                        resource_id=issue_key,
                        name=f"[{issue_key}] {summary}",
                        metadata={
                            "issue_key": issue_key,
                            "summary": summary,
                            "status": status_name,
                            "priority": priority,
                            "project": project_key,
                            "updated": fields.get("updated", ""),
                            "url": f"{self.base_url}/browse/{issue_key}",
                        },
                    )
                )

            start_at += len(issues)
            total = payload.get("total", 0)
            if start_at >= total:
                break

        return sorted(resources, key=lambda r: r.resource_id)

    def fetch_resource(self, resource_id: str) -> dict[str, object]:
        """Fetch full details and comments for an issue, formatted as Markdown."""
        try:
            issue = self._request_json(f"/rest/api/3/issue/{quote(resource_id)}")
        except Exception:
            issue = self._request_json(f"/rest/api/2/issue/{quote(resource_id)}")

        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        status = fields.get("status", {}).get("name", "")
        priority = fields.get("priority", {}).get("name", "")
        issue_type = fields.get("issuetype", {}).get("name", "")
        raw_description = fields.get("description", "")
        description_text = str(raw_description) if raw_description else "No description provided."

        # Collect resolution comments
        comments_payload = fields.get("comment", {}).get("comments", [])
        comments_section = ""
        if comments_payload:
            comments_section = "\n\n### Resolution & Comments:\n"
            for c in comments_payload[-5:]:  # Last 5 comments
                author = c.get("author", {}).get("displayName", "User")
                body = c.get("body", "")
                created = c.get("created", "")
                comments_section += f"- **{author}** ({created}): {body}\n"

        content = (
            f"# [{resource_id}] {summary}\n\n"
            f"**Type:** {issue_type} | **Status:** {status} | **Priority:** {priority}\n\n"
            f"### Description:\n{description_text}"
            f"{comments_section}"
        )

        return {
            "resource_id": resource_id,
            "path": f"jira://{resource_id}",
            "relative_path": f"{resource_id}.md",
            "content": content,
            "metadata": {
                "source_type": "jira",
                "issue_key": resource_id,
                "summary": summary,
                "status": status,
                "priority": priority,
                "url": f"{self.base_url}/browse/{resource_id}",
            },
        }
