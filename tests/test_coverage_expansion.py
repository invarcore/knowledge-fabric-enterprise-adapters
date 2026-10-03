"""Comprehensive test coverage expansion for enterprise and SaaS adapters.

Covers edge cases, pagination, error paths, tenant filtering, authentication modes,
format extraction, and security redactors across enterprise_adapters and knowledge_fabric_adapters.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
import urllib.error
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from enterprise_adapters.approval_storage import SQLiteApprovalStore
from enterprise_adapters.content_sanitizer import sanitize_document_content
from enterprise_adapters.contracts import ReadOnlyResource
from enterprise_adapters.policy import PolicyDecision, PolicyDecisionType
from enterprise_adapters.execution import ApprovedRuntimeActionAdapter
from enterprise_adapters.file_source_adapters import ApprovedFileStoreSourceAdapter
from enterprise_adapters.github_issue_adapter import GitHubIssueAdapter
from enterprise_adapters.mcp_server import (
    EnterpriseAdaptersMCPTools,
    create_mcp_server,
    main as mcp_main,
)
from enterprise_adapters.runtime_adapters import ReadOnlyRuntimeDiscoveryAdapter
from enterprise_adapters.runtime_snapshot_adapters import RuntimeSnapshotDiscoveryAdapter
from enterprise_adapters.server import serve
from enterprise_adapters.simulation import SimulationOnlyRuntimeActionAdapter
from enterprise_adapters.source_adapters import PrivateMarkdownSourceAdapter
from enterprise_adapters.standard_source_adapters import (
    AllowlistedDocumentSourceAdapter,
    AllowlistedWebsiteSourceAdapter,
    GitHubRepositorySourceAdapter,
    _extract_docx_text,
    _extract_web_content,
    _normalize_url,
    _url_name,
)
from knowledge_fabric_adapters.connectors.confluence import (
    ConfluenceSourceAdapter,
    _ConfluenceHTMLToMarkdown,
)
from knowledge_fabric_adapters.connectors.google_drive import GoogleDriveSourceAdapter
from knowledge_fabric_adapters.connectors.jira import JiraSourceAdapter
from knowledge_fabric_adapters.connectors.notion import NotionSourceAdapter, _block_to_markdown
from knowledge_fabric_adapters.security import (
    RedactingLoggingFilter,
    sanitize_exception,
    sanitize_log_message,
)


# ==============================================================================
# 1. Google Drive Connector Edge Cases & Error Paths
# ==============================================================================

def test_google_drive_headers_and_type() -> None:
    adapter = GoogleDriveSourceAdapter(access_token="test_token_xyz")
    assert adapter.source_type == "google_drive"
    headers = adapter._headers()
    assert headers["Authorization"] == "Bearer test_token_xyz"
    assert "User-Agent" in headers


def test_google_drive_list_resources_and_pagination() -> None:
    adapter = GoogleDriveSourceAdapter(access_token="test_token", folder_id="folder_1")

    page_1 = {
        "files": [
            {
                "id": "subfolder_1",
                "name": "Nested Folder",
                "mimeType": "application/vnd.google-apps.folder",
            },
            {
                "id": "doc_1",
                "name": "Design Spec",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-02-01T00:00:00Z",
                "size": 1024,
            },
        ],
        "nextPageToken": "token_page_2",
    }
    page_2 = {
        "files": [
            {
                "id": "sheet_1",
                "name": "Metrics",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "modifiedTime": "2026-02-02T00:00:00Z",
                "size": 2048,
            }
        ],
    }

    with patch.object(GoogleDriveSourceAdapter, "_request_json", side_effect=[page_1, page_2]):
        resources = adapter.list_resources()
        assert len(resources) == 2
        # Folder is skipped
        assert resources[0].resource_id == "doc_1"
        assert resources[0].name == "Design Spec"
        assert resources[1].resource_id == "sheet_1"


def test_google_drive_list_resources_error_breaks_gracefully() -> None:
    adapter = GoogleDriveSourceAdapter(access_token="test_token")
    with patch.object(GoogleDriveSourceAdapter, "_request_json", side_effect=RuntimeError("API error")):
        resources = adapter.list_resources()
        assert resources == []


def test_google_drive_fetch_resource_google_doc_export() -> None:
    adapter = GoogleDriveSourceAdapter(access_token="test_token")
    meta_payload = {
        "id": "doc_abc",
        "name": "Security Architecture",
        "mimeType": "application/vnd.google-apps.document",
        "modifiedTime": "2026-01-15T10:00:00Z",
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"Zero Trust Network Architecture details."
    mock_resp.__enter__.return_value = mock_resp

    with patch.object(GoogleDriveSourceAdapter, "_request_json", return_value=meta_payload), \
         patch("knowledge_fabric_adapters.connectors.google_drive.urlopen", return_value=mock_resp):
        res = adapter.fetch_resource("doc_abc")
        assert res["resource_id"] == "doc_abc"
        assert res["path"] == "gdrive://doc_abc"
        assert "# Security Architecture" in str(res["content"])
        assert "Zero Trust Network Architecture details." in str(res["content"])


def test_google_drive_fetch_resource_binary_download() -> None:
    adapter = GoogleDriveSourceAdapter(access_token="test_token")
    meta_payload = {
        "id": "file_123",
        "name": "config.yaml",
        "mimeType": "application/x-yaml",
        "modifiedTime": "2026-01-16T12:00:00Z",
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"env: production\nreplicas: 3"
    mock_resp.__enter__.return_value = mock_resp

    with patch.object(GoogleDriveSourceAdapter, "_request_json", return_value=meta_payload), \
         patch("knowledge_fabric_adapters.connectors.google_drive.urlopen", return_value=mock_resp):
        res = adapter.fetch_resource("file_123")
        assert res["resource_id"] == "file_123"
        assert "env: production" in str(res["content"])


def test_google_drive_request_json_error_handling() -> None:
    adapter = GoogleDriveSourceAdapter(access_token="test_token")
    with patch("knowledge_fabric_adapters.connectors.google_drive.urlopen", side_effect=ValueError("connection reset")):
        with pytest.raises(RuntimeError, match="Google Drive request failed"):
            adapter._request_json("https://www.googleapis.com/drive/v3/files")


# ==============================================================================
# 2. Notion Connector Edge Cases & Rich Block Variations
# ==============================================================================

def test_notion_block_to_markdown_all_variants() -> None:
    h1 = {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Header 1"}]}}
    h3 = {"type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "Header 3"}]}}
    num = {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"plain_text": "Step"}]}}
    todo_done = {"type": "to_do", "to_do": {"checked": True, "rich_text": [{"plain_text": "Done task"}]}}
    todo_pending = {"type": "to_do", "to_do": {"checked": False, "rich_text": [{"plain_text": "Open task"}]}}
    code_block = {"type": "code", "code": {"language": "python", "rich_text": [{"plain_text": "x = 42"}]}}
    quote_block = {"type": "quote", "quote": {"rich_text": [{"plain_text": "To be or not to be"}]}}
    callout_emoji = {"type": "callout", "callout": {"icon": {"emoji": "⚠️"}, "rich_text": [{"plain_text": "Warning"}]}}
    callout_default = {"type": "callout", "callout": {"icon": {}, "rich_text": [{"plain_text": "Info"}]}}
    divider = {"type": "divider"}
    unknown = {"type": "unsupported_block_type"}

    assert _block_to_markdown(h1) == "# Header 1\n\n"
    assert _block_to_markdown(h3) == "### Header 3\n\n"
    assert _block_to_markdown(num) == "1. Step\n"
    assert _block_to_markdown(todo_done) == "- [x] Done task\n"
    assert _block_to_markdown(todo_pending) == "- [ ] Open task\n"
    assert _block_to_markdown(code_block) == "```python\nx = 42\n```\n\n"
    assert _block_to_markdown(quote_block) == "> To be or not to be\n\n"
    assert _block_to_markdown(callout_emoji) == "> ⚠️ Warning\n\n"
    assert "> ℹ️ Info" in _block_to_markdown(callout_default)
    assert _block_to_markdown(divider) == "---\n\n"
    assert _block_to_markdown(unknown) == ""


def test_notion_list_resources_page_ids_and_database_pagination() -> None:
    adapter = NotionSourceAdapter(
        api_key="secret_test",
        database_ids=["db_1"],
        page_ids=["page_root_1", "page_root_failing"],
    )

    db_page_1 = {
        "results": [
            {
                "id": "p_db_1",
                "properties": {"Name": {"type": "title", "title": [{"plain_text": "Roadmap"}]}},
                "created_time": "2026-01-01T00:00:00Z",
                "last_edited_time": "2026-01-02T00:00:00Z",
                "url": "https://notion.so/p_db_1",
            }
        ],
        "has_more": True,
        "next_cursor": "cursor_2",
    }
    db_page_2 = {
        "results": [
            {
                "id": "p_db_2",
                "properties": {},  # untitled
                "created_time": "2026-01-03T00:00:00Z",
            }
        ],
        "has_more": False,
    }

    page_1_data = {
        "id": "page_root_1",
        "properties": {"Title": {"type": "title", "title": [{"plain_text": "Company Handbook"}]}},
        "url": "https://notion.so/page_root_1",
    }

    def mock_request(path: str, method: str = "GET", payload: dict | None = None):
        if "/databases/db_1/query" in path:
            if payload and payload.get("start_cursor") == "cursor_2":
                return db_page_2
            return db_page_1
        elif path == "/pages/page_root_1":
            return page_1_data
        elif path == "/pages/page_root_failing":
            raise RuntimeError("Page access forbidden")
        raise ValueError(f"Unexpected path: {path}")

    with patch.object(adapter, "_request", side_effect=mock_request):
        resources = adapter.list_resources()
        assert len(resources) == 3
        ids = [r.resource_id for r in resources]
        assert "p_db_1" in ids
        assert "p_db_2" in ids
        assert "page_root_1" in ids


def test_notion_fetch_resource_with_block_pagination() -> None:
    adapter = NotionSourceAdapter(api_key="secret_test")

    page_payload = {
        "id": "notion_p1",
        "properties": {"Name": {"type": "title", "title": [{"plain_text": "Incident Retrospective"}]}},
        "url": "https://notion.so/notion_p1",
    }
    blocks_batch_1 = {
        "results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Timeline of events."}]}}],
        "has_more": True,
        "next_cursor": "block_cursor_next",
    }
    blocks_batch_2 = {
        "results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Action items resolved."}]}}],
        "has_more": False,
    }

    def mock_request(path: str, method: str = "GET", payload: dict | None = None):
        if path == "/pages/notion_p1":
            return page_payload
        if "/blocks/notion_p1/children" in path:
            if "start_cursor=block_cursor_next" in path:
                return blocks_batch_2
            return blocks_batch_1
        raise ValueError(path)

    with patch.object(adapter, "_request", side_effect=mock_request):
        res = adapter.fetch_resource("notion_p1")
        assert res["resource_id"] == "notion_p1"
        assert "# Incident Retrospective" in str(res["content"])
        assert "Timeline of events." in str(res["content"])
        assert "Action items resolved." in str(res["content"])


def test_notion_request_error_handling() -> None:
    adapter = NotionSourceAdapter(api_key="secret_test")
    with patch("knowledge_fabric_adapters.connectors.notion.urlopen", side_effect=ValueError("DNS resolution failed")):
        with pytest.raises(RuntimeError, match="Notion request failed"):
            adapter._request("/users")


# ==============================================================================
# 3. Jira Connector Edge Cases, Auth & v2 Fallback
# ==============================================================================

def test_jira_headers_basic_and_bearer() -> None:
    adapter_basic = JiraSourceAdapter(
        base_url="https://jira.company.com/",
        email="eng@company.com",
        api_token="token123",
    )
    assert adapter_basic.base_url == "https://jira.company.com"
    headers_basic = adapter_basic._headers()
    assert headers_basic["Authorization"].startswith("Basic ")

    adapter_bearer = JiraSourceAdapter(
        base_url="https://jira.company.com",
        api_token="personal_access_token",
    )
    headers_bearer = adapter_bearer._headers()
    assert headers_bearer["Authorization"] == "Bearer personal_access_token"


def test_jira_list_resources_v2_fallback_and_pagination() -> None:
    adapter = JiraSourceAdapter(base_url="https://jira.company.com")

    # API v3 search fails, v2 search succeeds with 2 issues across pagination
    batch_1 = {
        "issues": [{"key": "SEC-1", "fields": {"summary": "Issue 1"}}],
        "total": 2,
    }
    batch_2 = {
        "issues": [{"key": "SEC-2", "fields": {"summary": "Issue 2"}}],
        "total": 2,
    }

    calls = 0

    def mock_request(path: str, params: dict | None = None):
        nonlocal calls
        calls += 1
        if path == "/rest/api/3/search":
            raise RuntimeError("v3 not supported")
        if path == "/rest/api/2/search":
            if params and params.get("startAt") == 0:
                return batch_1
            return batch_2
        raise ValueError(path)

    with patch.object(adapter, "_request_json", side_effect=mock_request):
        resources = adapter.list_resources()
        assert len(resources) == 2
        assert resources[0].resource_id == "SEC-1"
        assert resources[1].resource_id == "SEC-2"


def test_jira_fetch_resource_v2_fallback_and_missing_description() -> None:
    adapter = JiraSourceAdapter(base_url="https://jira.company.com")

    v2_issue = {
        "key": "DEV-99",
        "fields": {
            "summary": "Fix connection timeout",
            "status": {"name": "Closed"},
            "priority": {"name": "Medium"},
            "issuetype": {"name": "Bug"},
            "description": None,  # Test fallback to 'No description provided.'
            "comment": {"comments": []},
        },
    }

    def mock_request(path: str, params: dict | None = None):
        if "/rest/api/3/issue/" in path:
            raise RuntimeError("v3 not supported")
        if "/rest/api/2/issue/" in path:
            return v2_issue
        raise ValueError(path)

    with patch.object(adapter, "_request_json", side_effect=mock_request):
        res = adapter.fetch_resource("DEV-99")
        assert res["resource_id"] == "DEV-99"
        assert "No description provided." in str(res["content"])
        assert "DEV-99" in str(res["content"])


def test_jira_request_json_error_handling() -> None:
    adapter = JiraSourceAdapter(base_url="https://jira.company.com")
    with patch("knowledge_fabric_adapters.connectors.jira.urlopen", side_effect=ValueError("connection refused")):
        with pytest.raises(RuntimeError, match="Jira request failed"):
            adapter._request_json("/rest/api/3/issue/TEST-1")


# ==============================================================================
# 4. Confluence Connector HTML Parsing & Edge Cases
# ==============================================================================

def test_confluence_headers_and_auth() -> None:
    adapter = ConfluenceSourceAdapter(
        base_url="https://wiki.company.com/",
        space_keys=["ENG"],
        email="dev@company.com",
        api_token="pass123",
    )
    assert adapter.base_url == "https://wiki.company.com"
    headers = adapter._headers()
    assert headers["Authorization"].startswith("Basic ")


def test_confluence_html_parser_all_tags() -> None:
    html = """
    <h3>Section 3</h3>
    <h4>Section 4</h4>
    <h5>Section 5</h5>
    <h6>Section 6</h6>
    <pre>code snippet</pre>
    <ac:structured-macro>macro body</ac:structured-macro>
    """
    parser = _ConfluenceHTMLToMarkdown()
    parser.feed(html)
    text = parser.get_text()
    assert "### Section 3" in text
    assert "#### Section 4" in text
    assert "##### Section 5" in text
    assert "###### Section 6" in text
    assert "code snippet" in text
    assert "macro body" in text


def test_confluence_list_resources_pagination_and_error() -> None:
    adapter = ConfluenceSourceAdapter(
        base_url="https://wiki.company.com",
        space_keys=["ENG", "FAIL"],
    )

    eng_results = {
        "results": [
            {
                "id": "10",
                "title": "Onboarding",
                "version": {"number": 1, "when": "2026-01-01"},
            }
        ]
    }

    def mock_request(path: str, params: dict | None = None):
        if params and params.get("spaceKey") == "FAIL":
            raise RuntimeError("Space not found")
        if params and params.get("start") == 0:
            return eng_results
        return {"results": []}

    with patch.object(adapter, "_request_json", side_effect=mock_request):
        resources = adapter.list_resources()
        assert len(resources) == 1
        assert resources[0].resource_id == "10"


def test_confluence_request_json_error_handling() -> None:
    adapter = ConfluenceSourceAdapter(base_url="https://wiki.company.com", space_keys=["ENG"])
    with patch("knowledge_fabric_adapters.connectors.confluence.urlopen", side_effect=ValueError("timeout")):
        with pytest.raises(RuntimeError, match="Confluence request failed"):
            adapter._request_json("/rest/api/content")


# ==============================================================================
# 5. Server Lifecycle & CLI Entrypoints
# ==============================================================================

def test_server_serve_lifecycle() -> None:
    with patch("enterprise_adapters.server.AdapterHTTPServer") as mock_server_cls:
        mock_instance = mock_server_cls.return_value
        mock_instance.serve_forever.side_effect = KeyboardInterrupt()

        # Should execute cleanly without error and close server
        serve(host="127.0.0.1", port=9099)
        mock_instance.serve_forever.assert_called_once()
        mock_instance.server_close.assert_called_once()


def test_enterprise_adapters_main_cli() -> None:
    from enterprise_adapters.__main__ import main as cli_main
    assert cli_main() == 0


# ==============================================================================
# 6. MCP Server Tools, Tenant Isolation & Tool Decorators
# ==============================================================================

def test_mcp_tools_default_init_with_env(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"ENTERPRISE_ADAPTER_DOCS_ROOT": str(tmp_path)}):
        tools = EnterpriseAdaptersMCPTools()
        status = tools.health_check()
        assert "documents" in status["registered_sources"]


def test_mcp_tools_list_sources_tenant_filtering() -> None:
    mock_adapter = MagicMock()
    mock_adapter.list_resources.return_value = [
        ReadOnlyResource(resource_id="tenant-A/doc1.md", name="Doc A", metadata={"tenant_id": "tenant-A"}),
        ReadOnlyResource(resource_id="tenant-B/doc2.md", name="Doc B", metadata={"tenant_id": "tenant-B"}),
        ReadOnlyResource(resource_id="shared.md", name="Shared Doc", metadata={"tenant_id": "tenant-A"}),
    ]

    tools = EnterpriseAdaptersMCPTools({"test": mock_adapter})

    res_a = tools.list_sources(tenant_id="tenant-A")
    assert res_a["tenant_id"] == "tenant-A"
    assert res_a["sources"][0]["resource_count"] == 2

    res_b = tools.list_sources(tenant_id="tenant-B")
    assert res_b["tenant_id"] == "tenant-B"
    assert res_b["sources"][0]["resource_count"] == 1


def test_mcp_tools_fetch_document_tenant_isolation_and_errors() -> None:
    mock_adapter = MagicMock()
    mock_adapter.fetch_resource.return_value = {
        "content": "Secret tenant A data",
        "path": "/internal/server/path.md",
        "metadata": {"source_root": "/var/data", "name": "Doc A"},
    }

    tools = EnterpriseAdaptersMCPTools({"tenant_source": mock_adapter})

    # 1. Unknown source error
    with pytest.raises(ValueError, match="Source adapter 'unknown' not found"):
        tools.fetch_document("unknown", "doc.md")

    # 2. Adapter without fetch_resource
    tools.register_adapter("no_fetch", object())
    with pytest.raises(ValueError, match="does not support fetch_resource"):
        tools.fetch_document("no_fetch", "doc.md")

    # 3. Tenant mismatch error
    with pytest.raises(PermissionError, match="belongs to tenant 'tenant-B', not 'tenant-A'"):
        tools.fetch_document("tenant_source", "tenant-B/payroll.md", tenant_id="tenant-A")

    # 4. Valid fetch strips path and source_root leaks
    result = tools.fetch_document("tenant_source", "tenant-A/doc.md", tenant_id="tenant-A")
    assert "path" not in result
    assert "source_root" not in result["metadata"]
    assert result["metadata"]["tenant_id"] == "tenant-A"


def test_create_mcp_server_invocations(monkeypatch) -> None:
    from types import ModuleType
    fastmcp_mock = MagicMock()
    fastmcp_cls = MagicMock(return_value=fastmcp_mock)
    fake_mod = ModuleType("mcp.server.fastmcp")
    fake_mod.FastMCP = fastmcp_cls
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_mod)

    tools = EnterpriseAdaptersMCPTools()
    server = create_mcp_server(tools)
    assert server is not None

    # Verify tool execution via the tool decorators registered on FastMCP
    health = tools.health_check()
    assert health["status"] == "healthy"

    sources = tools.list_sources()
    assert "sources" in sources


def test_mcp_main_invocation() -> None:
    with patch("enterprise_adapters.mcp_server.create_mcp_server") as mock_create:
        mock_server = MagicMock()
        mock_create.return_value = mock_server
        mcp_main()
        mock_server.run.assert_called_once()


# ==============================================================================
# 7. Standard & File Source Adapters Edge Cases & Formats
# ==============================================================================

def test_github_repo_adapter_from_env_and_empty_paths() -> None:
    # 1. Missing repo
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(EnvironmentError, match="GITHUB_SOURCE_REPOSITORY"):
            GitHubRepositorySourceAdapter.from_env()

    # 2. Missing allowlist
    with patch.dict(os.environ, {"GITHUB_SOURCE_REPOSITORY": "owner/repo"}, clear=True):
        with pytest.raises(EnvironmentError, match="allowed_paths or GITHUB_SOURCE_ALLOWLIST"):
            GitHubRepositorySourceAdapter.from_env()

    # 3. Successful from_env
    env = {
        "GITHUB_SOURCE_REPOSITORY": "owner/repo",
        "GITHUB_SOURCE_ALLOWLIST": "docs,src/readme.md",
        "GITHUB_SOURCE_REF": "release-v1",
        "GITHUB_TOKEN": "gh_token_123",
    }
    with patch.dict(os.environ, env, clear=True):
        adapter = GitHubRepositorySourceAdapter.from_env()
        assert adapter.repository == "owner/repo"
        assert adapter.allowed_paths == ["docs", "src/readme.md"]
        assert adapter.ref == "release-v1"
        assert adapter.token == "gh_token_123"


def test_github_repo_adapter_directory_fetch_error() -> None:
    adapter = GitHubRepositorySourceAdapter(repository="owner/repo", allowed_paths=["docs"])
    # If API returns a list, it's a directory
    with patch.object(GitHubRepositorySourceAdapter, "_request_json", return_value=[{"name": "file1.md"}]):
        with pytest.raises(ValueError, match="resolved to a directory"):
            adapter.fetch_resource("docs")


def test_allowlisted_document_adapter_edge_cases(tmp_path: Path) -> None:
    # 1. Empty allowed roots
    with pytest.raises(ValueError, match="allowed_roots must not be empty"):
        AllowlistedDocumentSourceAdapter(source_root=tmp_path, allowed_roots=[])

    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    adapter = AllowlistedDocumentSourceAdapter(source_root=tmp_path, allowed_roots=[doc_dir])

    # 2. Escaping source root
    with pytest.raises(ValueError, match="escapes the approved source root"):
        adapter.fetch_resource("../outside.txt")

    # 3. Not found
    with pytest.raises(FileNotFoundError, match="Resource not found"):
        adapter.fetch_resource("docs/nonexistent.md")

    # 4. Unsupported file type
    unsupported = doc_dir / "binary.bin"
    unsupported.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError, match="Unsupported file type"):
        adapter.fetch_resource("docs/binary.bin")


def test_document_adapter_docx_text_extraction(tmp_path: Path) -> None:
    # Create a real in-memory docx structure with word/document.xml
    docx_file = tmp_path / "test_doc.docx"
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>Enterprise Architecture Blueprint</w:t></w:r></w:p>
            <w:p><w:r><w:t>Section 1: Microservices</w:t></w:r></w:p>
        </w:body>
    </w:document>
    """
    with zipfile.ZipFile(docx_file, "w") as zf:
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))

    extracted = _extract_docx_text(docx_file)
    assert "Enterprise Architecture Blueprint" in extracted
    assert "Section 1: Microservices" in extracted


def test_web_content_extraction_scripts_and_styles() -> None:
    raw_html = """
    <html>
        <head>
            <style>body { font-size: 14px; }</style>
            <script>console.log("secret tracker");</script>
        </head>
        <body>
            <h1>Main Title</h1>
            <p>Body paragraph with information.</p>
        </body>
    </html>
    """
    extracted = _extract_web_content(raw_html, "text/html")
    assert "Main Title" in extracted
    assert "Body paragraph with information." in extracted
    assert "font-size" not in extracted
    assert "secret tracker" not in extracted


def test_website_adapter_url_helpers() -> None:
    # Invalid url raises ValueError
    with pytest.raises(ValueError, match="Invalid URL"):
        _normalize_url("ftp://")

    assert _url_name("https://example.com/api/v1/spec") == "spec"
    assert _url_name("https://example.com") == "example.com"


def test_approved_file_store_and_markdown_adapter_edge_cases(tmp_path: Path) -> None:
    # Empty allowed roots
    with pytest.raises(ValueError, match="allowed_roots must not be empty"):
        ApprovedFileStoreSourceAdapter(source_root=tmp_path, allowed_roots=[])

    with pytest.raises(ValueError, match="allowed_roots must not be empty"):
        PrivateMarkdownSourceAdapter(source_root=tmp_path, allowed_roots=[])

    sub = tmp_path / "sub"
    sub.mkdir()
    adapter = ApprovedFileStoreSourceAdapter(source_root=tmp_path, allowed_roots=[sub])

    # Unsupported extension
    unsupported = sub / "video.mp4"
    unsupported.write_bytes(b"data")
    with pytest.raises(ValueError, match="Unsupported file type"):
        adapter.fetch_resource("sub/video.mp4")

    # Escaping root
    with pytest.raises(ValueError, match="escapes the approved source root"):
        adapter.fetch_resource("../outside.py")

    # Not found
    with pytest.raises(FileNotFoundError, match="Resource not found"):
        adapter.fetch_resource("sub/missing.py")


# ==============================================================================
# 8. GitHub Issue Adapter Edge Cases & HTTPError Handling
# ==============================================================================

def test_github_issue_adapter_from_env() -> None:
    # 1. Missing token
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(EnvironmentError, match="GITHUB_TOKEN"):
            GitHubIssueAdapter.from_env()

    # 2. Missing repo
    with patch.dict(os.environ, {"GITHUB_TOKEN": "token"}, clear=True):
        with pytest.raises(EnvironmentError, match="GITHUB_ISSUE_REPO"):
            GitHubIssueAdapter.from_env()

    # 3. Valid from_env
    with patch.dict(os.environ, {"GITHUB_TOKEN": "token", "GITHUB_ISSUE_REPO": "org/repo"}, clear=True):
        adapter = GitHubIssueAdapter.from_env()
        assert adapter._token == "token"
        assert adapter._repo == "org/repo"


def test_github_issue_adapter_http_error() -> None:
    adapter = GitHubIssueAdapter(token="tok", repo="org/repo")
    action = {
        "action_name": "create_issue",
        "approval_id": "app_1",
        "policy_decision": PolicyDecision(
            decision_id="dec_issue",
            decision=PolicyDecisionType.ALLOW,
            reasons=["approved"],
        ),
    }

    err = urllib.error.HTTPError(
        url="https://api.github.com",
        code=403,
        msg="Forbidden",
        hdrs=MagicMock(),
        fp=io.BytesIO(b'{"message": "Rate limit exceeded"}'),
    )

    with patch.dict(os.environ, {"FABRIC_APPROVAL_ENFORCEMENT": "permissive"}), \
         patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="GitHub API error 403: {\"message\": \"Rate limit exceeded\"}"):
            adapter.execute_action(action)


# ==============================================================================
# 9. Security Redaction, Logging Filter & Storage Edge Cases
# ==============================================================================

def test_security_sanitize_log_non_string() -> None:
    assert sanitize_log_message(12345) == "12345"
    assert sanitize_log_message({"key": "val"}) == "{'key': 'val'}"


def test_security_redacting_filter_dict_and_tuple_args() -> None:
    log_filter = RedactingLoggingFilter()

    # Dict args
    record_dict = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="API call with args",
        args={"token": "Bearer secret_bearer_token_12345", "count": 10},
        exc_info=None,
    )
    assert log_filter.filter(record_dict) is True
    assert record_dict.args["token"] == "Bearer [REDACTED]"
    assert record_dict.args["count"] == 10

    # Tuple args
    record_tuple = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="User authenticated: %s, role: %s",
        args=("Bearer secret_bearer_token_12345", "admin"),
        exc_info=None,
    )
    assert log_filter.filter(record_tuple) is True
    assert record_tuple.args[0] == "Bearer [REDACTED]"
    assert record_tuple.args[1] == "admin"


def test_content_sanitizer_edge_cases() -> None:
    assert sanitize_document_content(None) == ""
    assert sanitize_document_content(123) == "123"

    with patch.dict(os.environ, {"ADAPTERS_REDACT_SECRETS": "false"}):
        sensitive = "AKIA1234567890ABCDEF"
        assert sanitize_document_content(sensitive) == sensitive


def test_execution_adapter_edge_cases() -> None:
    adapter = ApprovedRuntimeActionAdapter()

    # Missing action name
    with pytest.raises(ValueError, match="action_name is required"):
        adapter.prepare_action({})

    # Policy decision is not ALLOW
    deny_action = {
        "action_name": "delete_db",
        "policy_decision": PolicyDecision(decision_id="dec_deny", decision=PolicyDecisionType.DENY, reasons=["blocked"]),
    }
    with pytest.raises(PermissionError, match="Approved execution requires an allow policy decision"):
        adapter.execute_action(deny_action)

    # raw_step_ids as string
    with patch.dict(os.environ, {"FABRIC_APPROVAL_ENFORCEMENT": "permissive"}):
        valid_action = {
            "action_name": "restart_pod",
            "approval_id": "app_xyz",
            "step_ids": "single_step_id",
            "policy_decision": PolicyDecision(decision_id="dec_allow", decision=PolicyDecisionType.ALLOW, reasons=["ok"]),
        }
        receipt = adapter.execute_action(valid_action)
        assert receipt["status"] == "executed"


def test_simulation_adapter_policy_denied() -> None:
    adapter = SimulationOnlyRuntimeActionAdapter()
    action = {"action_name": "drop_tables"}
    policy = PolicyDecision(decision_id="dec_sim_deny", decision=PolicyDecisionType.DENY, reasons=["dangerous"])
    res = adapter.simulate_with_policy(action, policy)
    assert res["status"] == "blocked"
    assert "Policy denied the action." in res["logs"]


def test_runtime_discovery_unknown_target_error() -> None:
    adapter = ReadOnlyRuntimeDiscoveryAdapter.from_discovery(
        runtime_tools=[],
        case_types_or_equivalent=[],
    )
    with pytest.raises(KeyError, match="Unknown runtime target: missing_id"):
        adapter.get_metadata("missing_id")


def test_runtime_snapshot_unknown_target_error() -> None:
    adapter = RuntimeSnapshotDiscoveryAdapter.from_mapping({})
    with pytest.raises(KeyError, match="Unknown runtime target: missing_id"):
        adapter.get_metadata("missing_id")


def test_sqlite_approval_store_get_nonexistent(tmp_path: Path) -> None:
    db_file = tmp_path / "approvals.db"
    store = SQLiteApprovalStore(db_file)
    assert store.get("nonexistent_id") is None


def test_execution_adapter_step_ids_string_with_write() -> None:
    adapter = ApprovedRuntimeActionAdapter()
    with patch.dict(os.environ, {"FABRIC_APPROVAL_ENFORCEMENT": "permissive"}):
        action = {
            "action_name": "mutative_task",
            "write": True,
            "approval_id": "app_write",
            "step_ids": "single_step_string",
            "policy_decision": PolicyDecision("d_allow", PolicyDecisionType.ALLOW),
        }
        receipt = adapter.execute_action(action)
        assert receipt["status"] == "executed"


def test_mcp_tools_list_sources_prefix_filtering_no_tenant_metadata() -> None:
    mock_adapter = MagicMock()
    mock_adapter.list_resources.return_value = [
        ReadOnlyResource(resource_id="tenant-CorpA/file1.md", name="Corp A Doc", metadata={}),
        ReadOnlyResource(resource_id="tenant-CorpB/file2.md", name="Corp B Doc", metadata={}),
    ]
    tools = EnterpriseAdaptersMCPTools({"corp_source": mock_adapter})
    res = tools.list_sources(tenant_id="tenant-CorpA")
    assert res["sources"][0]["resource_count"] == 1
    assert res["sources"][0]["resources"][0]["resource_id"] == "tenant-CorpA/file1.md"


def test_connector_source_types_and_auth_modes() -> None:
    jira = JiraSourceAdapter(base_url="https://jira.local")
    assert jira.source_type == "jira"

    confluence = ConfluenceSourceAdapter(base_url="https://wiki.local", space_keys=["KNOW"], api_token="bearer_only_token")
    assert confluence.source_type == "confluence"
    headers = confluence._headers()
    assert headers["Authorization"] == "Bearer bearer_only_token"


def test_google_drive_request_json_success() -> None:
    adapter = GoogleDriveSourceAdapter(access_token="valid_token")
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"kind": "drive#fileList"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    with patch("knowledge_fabric_adapters.connectors.google_drive.urlopen", return_value=mock_resp):
        payload = adapter._request_json("https://www.googleapis.com/drive/v3/files")
        assert payload["kind"] == "drive#fileList"


def test_smoke_test_hermetic_pipeline() -> None:
    benchmarks_dir = str(Path(__file__).parent.parent / "benchmarks")
    if benchmarks_dir not in sys.path:
        sys.path.insert(0, benchmarks_dir)
    from live_adapter_smoke_test import run_hermetic_pipeline
    res = run_hermetic_pipeline()
    assert res["status"] == "success"
    assert res["signature_verified"] is True
    assert res["policy_decision"] == "requires_approval"


