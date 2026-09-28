from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from enterprise_adapters.mcp_server import EnterpriseAdaptersMCPTools, create_mcp_server
from enterprise_adapters.standard_source_adapters import AllowlistedDocumentSourceAdapter


class _FakeFastMCP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tool_names: list[str] = []
        self.tools: dict[str, object] = {}

    def tool(self, name: str):
        def _decorator(func):
            self.tool_names.append(name)
            self.tools[name] = func
            return func

        return _decorator

    def run(self) -> None:
        return


def test_enterprise_adapters_tools_health_check() -> None:
    tools = EnterpriseAdaptersMCPTools()
    hc = tools.health_check()
    assert hc["status"] == "healthy"
    assert hc["service"] == "enterprise-adapters"
    assert hc["redaction_active"] is True
    assert "schema_version" in hc


def test_enterprise_adapters_tools_list_and_fetch(tmp_path: Path) -> None:
    source_root = tmp_path / "approved"
    allowed_root = source_root / "docs"
    allowed_root.mkdir(parents=True)
    doc_path = allowed_root / "guide.md"
    doc_path.write_text("# Setup Guide\nUse token secret123 and configure.", encoding="utf-8")

    adapter = AllowlistedDocumentSourceAdapter(source_root=source_root, allowed_roots=[allowed_root])
    tools = EnterpriseAdaptersMCPTools(adapters={"docs": adapter})

    sources = tools.list_sources(tenant_id="acme")
    assert sources["tenant_id"] == "acme"
    assert len(sources["sources"]) == 1
    assert sources["sources"][0]["source"] == "docs"
    assert sources["sources"][0]["resource_count"] == 1

    payload = tools.fetch_document(source="docs", resource_id="docs/guide.md", tenant_id="acme")
    assert "Setup Guide" in payload["content"]
    assert payload["metadata"]["tenant_id"] == "acme"


def test_enterprise_adapters_tools_fetch_unknown_source() -> None:
    tools = EnterpriseAdaptersMCPTools()
    with pytest.raises(ValueError, match="Source adapter 'unknown' not found"):
        tools.fetch_document(source="unknown", resource_id="doc.md")


def test_create_mcp_server_registers_expected_tools(monkeypatch) -> None:
    fastmcp_module = ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = _FakeFastMCP
    monkeypatch.setitem(__import__("sys").modules, "mcp.server.fastmcp", fastmcp_module)

    server = create_mcp_server()
    assert server.name == "enterprise-adapters"
    assert sorted(server.tool_names) == ["fetch_document", "health_check", "list_sources"]
