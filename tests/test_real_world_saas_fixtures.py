"""Integration tests using real-world SaaS golden corpus fixtures.

Tests real-world Jira issue JSON, Confluence storage XHTML, and Notion Markdown
exports against enterprise source adapters and content sanitization.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from enterprise_adapters.content_sanitizer import sanitize_document_content
from enterprise_adapters.contracts import ReadOnlyResource
from enterprise_adapters.standard_source_adapters import AllowlistedDocumentSourceAdapter
from knowledge_fabric_adapters.connectors.confluence import (
    ConfluenceSourceAdapter,
    _ConfluenceHTMLToMarkdown,
)
from knowledge_fabric_adapters.connectors.jira import JiraSourceAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "saas"


def test_real_world_jira_incident_parsing_and_sanitization() -> None:
    """Test realistic Jira incident post-mortem parsing and secret scrubbing."""
    jira_fixture_path = FIXTURES_DIR / "jira_real_issue.json"
    assert jira_fixture_path.exists(), f"Missing fixture: {jira_fixture_path}"

    with open(jira_fixture_path, encoding="utf-8") as f:
        jira_payload = json.load(f)

    adapter = JiraSourceAdapter(
        base_url="https://jira.enterprise.internal",
        jql="project = PROD AND issuetype = Incident",
        email="sre@enterprise.internal",
        api_token="dummy_token",
    )

    with patch.object(adapter, "_request_json") as mock_req:
        mock_req.return_value = jira_payload

        fetched = adapter.fetch_resource("PROD-8421")

        assert fetched["resource_id"] == "PROD-8421"
        assert fetched["path"] == "jira://PROD-8421"
        assert fetched["metadata"]["priority"] == "Critical"
        assert fetched["metadata"]["status"] == "Resolved"

        raw_content = str(fetched["content"])
        assert "CommitFailedException" in raw_content
        assert "CooperativeStickyAssignor" in raw_content
        assert "Sarah Chen (Staff SRE)" in raw_content
        assert "Marcus Vance (Principal Platform Eng)" in raw_content

        # Pass through sanitization
        sanitized = sanitize_document_content(raw_content)

        # Verify secrets are safely redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in sanitized
        assert "[REDACTED_AWS_KEY]" in sanitized
        assert "xoxb-mock-slack-triage-token-fixture-12345" not in sanitized
        assert "[REDACTED_SLACK_TOKEN]" in sanitized
        assert "SuperSecretPass123!" not in sanitized
        assert "postgres://telemetry_admin:[REDACTED]@db-replica.internal:5432/telemetry_db" in sanitized

        # Verify operational content is preserved
        assert "session.timeout.ms: 45000" in sanitized
        assert "heartbeat.interval.ms: 15000" in sanitized


def test_real_world_confluence_xhtml_storage_conversion() -> None:
    """Test realistic Confluence storage XHTML parsing with macros and redaction."""
    confluence_fixture_path = FIXTURES_DIR / "confluence_real_storage.xml"
    assert confluence_fixture_path.exists(), f"Missing fixture: {confluence_fixture_path}"

    with open(confluence_fixture_path, encoding="utf-8") as f:
        raw_xhtml = f.read()

    parser = _ConfluenceHTMLToMarkdown()
    parser.feed(raw_xhtml)
    converted_md = parser.get_text()

    # Verify structural elements extracted
    assert "# Architectural Decision Record (ADR-0042)" in converted_md
    assert "## Context and Problem Statement" in converted_md
    assert "## Decision: Istio Ambient Mesh Architecture" in converted_md
    assert "TLS_AES_256_GCM_SHA384" in converted_md
    assert "PeerAuthentication" in converted_md
    assert "STRICT" in converted_md

    # Sanitize and verify credentials scrubbed
    sanitized = sanitize_document_content(converted_md)

    assert "Basic dXNlcjpzdXBlcnNlY3JldA==" not in sanitized
    assert "Basic [REDACTED]" in sanitized
    assert "Bearer [REDACTED]" in sanitized
    assert "AdminPass2026!" not in sanitized
    assert "postgres://admin_ro:[REDACTED]@legacy-proxy.internal:5432/router_config" in sanitized


def test_real_world_confluence_adapter_end_to_end() -> None:
    """Test ConfluenceSourceAdapter fetch_resource with real-world fixture."""
    confluence_fixture_path = FIXTURES_DIR / "confluence_real_storage.xml"
    with open(confluence_fixture_path, encoding="utf-8") as f:
        raw_xhtml = f.read()

    api_payload = {
        "id": "98124",
        "title": "Architectural Decision Record (ADR-0042): Zero Trust Service Mesh Migration",
        "space": {"key": "ARCH"},
        "version": {"number": 4, "when": "2026-09-20T10:15:00.000Z"},
        "body": {
            "storage": {
                "value": raw_xhtml,
            }
        },
    }

    adapter = ConfluenceSourceAdapter(
        base_url="https://confluence.enterprise.internal/wiki",
        space_keys=["ARCH"],
        email="architect@enterprise.internal",
        api_token="token123",
    )

    with patch.object(adapter, "_request_json") as mock_req:
        mock_req.return_value = api_payload

        resource = adapter.fetch_resource("98124")
        assert resource["resource_id"] == "98124"
        assert resource["path"] == "confluence://ARCH/98124"
        assert resource["relative_path"] == "ARCH/98124.md"
        assert "Zero Trust Service Mesh Migration" in str(resource["content"])

        sanitized_content = sanitize_document_content(str(resource["content"]))
        assert "dXNlcjpzdXBlcnNlY3JldA==" not in sanitized_content
        assert "Basic [REDACTED]" in sanitized_content


def test_real_world_notion_markdown_ingestion_and_sanitizer() -> None:
    """Test ingestion of real Notion markdown export using AllowlistedDocumentSourceAdapter."""
    notion_file = FIXTURES_DIR / "notion_real_export.md"
    assert notion_file.exists(), f"Missing fixture: {notion_file}"

    doc_adapter = AllowlistedDocumentSourceAdapter(
        source_root=FIXTURES_DIR,
        allowed_roots=[FIXTURES_DIR],
    )

    resources = doc_adapter.list_resources()
    resource_ids = [r.resource_id for r in resources]
    assert "notion_real_export.md" in resource_ids

    fetched = doc_adapter.fetch_resource("notion_real_export.md")
    content = str(fetched["content"])

    # Verify document structure and markdown table intact
    assert "Active-Active Kubernetes Multi-Region Failover" in content
    assert "RPO Target" in content
    assert "CockroachDB" in content or "cockroach" in content

    # Verify secrets were scrubbed automatically by AllowlistedDocumentSourceAdapter
    assert "Bearer test_operator_session_token_xyz12345" not in content
    assert "Bearer [REDACTED]" in content
    assert "ghp_OperatorAutomationToken1234567890abcdef" not in content
    assert "[REDACTED_GITHUB_TOKEN]" in content
    assert "sk-proj-ProdOpenAIKey98765432101234567890" not in content
    assert "[REDACTED_API_KEY]" in content


def test_cross_saas_multi_source_evidence_aggregation() -> None:
    """Test aggregating evidence across Jira, Confluence, and Notion into a consistent package."""
    jira_fixture_path = FIXTURES_DIR / "jira_real_issue.json"
    with open(jira_fixture_path, encoding="utf-8") as f:
        jira_payload = json.load(f)

    confluence_fixture_path = FIXTURES_DIR / "confluence_real_storage.xml"
    with open(confluence_fixture_path, encoding="utf-8") as f:
        confluence_xhtml = f.read()

    jira_adapter = JiraSourceAdapter(base_url="https://jira.enterprise.internal")
    confluence_adapter = ConfluenceSourceAdapter(
        base_url="https://confluence.enterprise.internal/wiki",
        space_keys=["ARCH"],
    )
    doc_adapter = AllowlistedDocumentSourceAdapter(
        source_root=FIXTURES_DIR,
        allowed_roots=[FIXTURES_DIR],
    )

    with patch.object(jira_adapter, "_request_json", return_value=jira_payload), \
         patch.object(confluence_adapter, "_request_json", return_value={
             "id": "ADR-0042",
             "title": "Zero Trust Mesh",
             "space": {"key": "ARCH"},
             "body": {"storage": {"value": confluence_xhtml}},
         }):

        jira_doc = jira_adapter.fetch_resource("PROD-8421")
        confluence_doc = confluence_adapter.fetch_resource("ADR-0042")
        notion_doc = doc_adapter.fetch_resource("notion_real_export.md")

    # Combine into evidence bundle
    evidence_bundle = [
        ReadOnlyResource(
            resource_id=jira_doc["resource_id"],
            name=f"[{jira_doc['resource_id']}] {jira_doc['metadata']['summary']}",
            metadata={"source_type": "jira", **jira_doc["metadata"]},
        ),
        ReadOnlyResource(
            resource_id=confluence_doc["resource_id"],
            name=confluence_doc["metadata"]["title"],
            metadata={"source_type": "confluence", **confluence_doc["metadata"]},
        ),
        ReadOnlyResource(
            resource_id=notion_doc["resource_id"],
            name="Notion DR Runbook",
            metadata={"source_type": "notion_export", **notion_doc["metadata"]},
        ),
    ]

    assert len(evidence_bundle) == 3
    source_types = {e.metadata["source_type"] for e in evidence_bundle}
    assert source_types == {"jira", "confluence", "notion_export"}

    # Scrub all contents and ensure no secrets leak anywhere in the unified bundle
    all_content = "\n".join([
        sanitize_document_content(str(jira_doc["content"])),
        sanitize_document_content(str(confluence_doc["content"])),
        str(notion_doc["content"]),
    ])

    forbidden_secrets = [
        "AKIAIOSFODNN7EXAMPLE",
        "SuperSecretPass123!",
        "dXNlcjpzdXBlcnNlY3JldA==",
        "AdminPass2026!",
        "ghp_OperatorAutomationToken1234567890abcdef",
        "sk-proj-ProdOpenAIKey98765432101234567890",
    ]

    for secret in forbidden_secrets:
        assert secret not in all_content, f"Leaked sensitive token in aggregated evidence: {secret}"
