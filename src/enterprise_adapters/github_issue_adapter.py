# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""GitHub Issues write adapter — creates issues after policy approval.

This is the first real production adapter in the stack.

Environment variables required:
    GITHUB_TOKEN          personal access token or fine-grained PAT with Issues: write
    GITHUB_ISSUE_REPO     owner/repo to create issues in, e.g. sagarv48/knowledge-fabric-demo

Usage:
    from enterprise_adapters.github_issue_adapter import GitHubIssueAdapter
    adapter = GitHubIssueAdapter.from_env()
    receipt = adapter.execute_action(approved_action)
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from enterprise_adapters.audit import build_audit_trail
from enterprise_adapters.execution import ApprovedRuntimeActionAdapter, _require_policy_decision
from enterprise_adapters.policy import PolicyDecisionType


_GITHUB_API = "https://api.github.com"


class GitHubIssueAdapter(ApprovedRuntimeActionAdapter):
    """Creates GitHub Issues after policy + human approval.

    The action payload must contain:
        action_name      str   e.g. "create_github_issue"
        write            bool  True
        approval_id      str   the stored approval ID
        policy_decision  PolicyDecision  with decision=ALLOW
        payload          dict  with keys: title, body, labels (optional list[str])
    """

    def __init__(self, token: str, repo: str) -> None:
        super().__init__(audit_trail=build_audit_trail())
        self._token = token
        self._repo = repo  # owner/repo

    @classmethod
    def from_env(cls) -> "GitHubIssueAdapter":
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise EnvironmentError("GITHUB_TOKEN environment variable is required")
        repo = os.environ.get("GITHUB_ISSUE_REPO", "")
        if not repo:
            raise EnvironmentError(
                "GITHUB_ISSUE_REPO environment variable is required (format: owner/repo)"
            )
        return cls(token=token, repo=repo)

    def execute_action(self, action: dict) -> dict:
        policy_decision = _require_policy_decision(action)
        if policy_decision.decision is not PolicyDecisionType.ALLOW:
            raise PermissionError("Approved execution requires an allow policy decision.")

        approval_id = str(action.get("approval_id", "")).strip()
        if not approval_id:
            raise PermissionError("Approved execution requires an approval_id.")

        # Cryptographic verification before any external HTTP mutation
        self._verify_approval_signature(action, approval_id)

        prepared = self.prepare_action(action)


        inner = action.get("payload", {})
        title = str(inner.get("title", prepared["action_name"]))
        body = str(inner.get("body", "Created by the knowledge-fabric enterprise adapter."))
        labels = list(inner.get("labels", ["ai-generated", "needs-review"]))

        issue_url = f"{_GITHUB_API}/repos/{self._repo}/issues"
        request_body = json.dumps(
            {"title": title, "body": body, "labels": labels}
        ).encode("utf-8")

        request = urllib.request.Request(
            issue_url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8") if exc.fp else str(exc)
            raise RuntimeError(
                f"GitHub API error {exc.code}: {error_body}"
            ) from exc

        issue_number = result.get("number", "?")
        issue_html_url = result.get("html_url", "")

        receipt = {
            "execution_id": prepared["prepared_action_id"],
            "action_name": action["action_name"],
            "status": "executed",
            "approval_id": action["approval_id"],
            "logs": [f"GitHub issue #{issue_number} created: {issue_html_url}"],
            "metadata": {
                "issue_number": issue_number,
                "issue_url": issue_html_url,
                "repo": self._repo,
            },
        }
        self._audit_trail.record(
            "github_issue.created",
            execution_id=receipt["execution_id"],
            action_name=receipt["action_name"],
            approval_id=receipt["approval_id"],
            issue_number=issue_number,
            issue_url=issue_html_url,
            repo=self._repo,
        )
        return receipt