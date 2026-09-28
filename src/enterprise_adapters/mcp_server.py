"""MCP server entrypoint for Enterprise Source Adapters."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from enterprise_adapters.contracts import ReadOnlyResource
from enterprise_adapters.standard_source_adapters import AllowlistedDocumentSourceAdapter

logger = logging.getLogger(__name__)

MCP_SCHEMA_VERSION = "0.1.0"


class EnterpriseAdaptersMCPTools:
    """MCP tool implementations for Enterprise Adapters."""

    def __init__(self, adapters: dict[str, Any] | None = None) -> None:
        self._adapters: dict[str, Any] = {}
        if adapters:
            self._adapters.update(adapters)
        else:
            self._init_default_adapters()

    def _init_default_adapters(self) -> None:
        docs_root_env = os.environ.get("ENTERPRISE_ADAPTER_DOCS_ROOT") or os.environ.get("SOURCE_ROOT")
        if docs_root_env:
            root_path = Path(docs_root_env).expanduser().resolve()
            if root_path.exists():
                self._adapters["documents"] = AllowlistedDocumentSourceAdapter(
                    source_root=root_path,
                    allowed_roots=[root_path],
                )

    def register_adapter(self, name: str, adapter: Any) -> None:
        """Register a source adapter under a unique name."""
        self._adapters[name] = adapter

    def health_check(self) -> dict[str, object]:
        """Check enterprise adapters health and return registered sources and sanitization posture."""
        return {
            "status": "healthy",
            "service": "enterprise-adapters",
            "registered_sources": sorted(list(self._adapters.keys())),
            "redaction_active": True,
            "schema_version": MCP_SCHEMA_VERSION,
        }

    def list_sources(self, tenant_id: str | None = None) -> dict[str, object]:
        """List all available source adapters and their allowlisted resources.

        Parameters:
        - tenant_id: Optional tenant identifier for scoping access.
        """
        sources_summary: list[dict[str, object]] = []
        for name, adapter in self._adapters.items():
            resources: list[ReadOnlyResource] = []
            if hasattr(adapter, "list_resources"):
                resources = adapter.list_resources()
            sources_summary.append({
                "source": name,
                "resource_count": len(resources),
                "resources": [
                    {
                        "resource_id": r.resource_id,
                        "name": r.name,
                        "metadata": r.metadata,
                    }
                    for r in resources
                ],
            })
        return {
            "tenant_id": tenant_id or os.environ.get("KF_DEFAULT_TENANT") or "default",
            "sources": sources_summary,
        }

    def fetch_document(
        self,
        source: str,
        resource_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, object]:
        """Fetch sanitized document content and metadata from an approved source adapter.

        Parameters:
        - source: Name of the registered source adapter (e.g. 'documents', 'github').
        - resource_id: Relative resource path or identifier within the adapter.
        - tenant_id: Optional tenant identifier.
        """
        adapter = self._adapters.get(source)
        if not adapter:
            raise ValueError(
                f"Source adapter '{source}' not found. Available sources: {sorted(list(self._adapters.keys()))}"
            )
        if not hasattr(adapter, "fetch_resource"):
            raise ValueError(f"Source adapter '{source}' does not support fetch_resource")

        payload = adapter.fetch_resource(resource_id)
        if tenant_id and isinstance(payload.get("metadata"), dict):
            payload["metadata"]["tenant_id"] = tenant_id
        return payload


def create_mcp_server(tools: EnterpriseAdaptersMCPTools | None = None) -> Any:
    """Create FastMCP server for Enterprise Adapters."""
    try:
        from mcp.server.fastmcp import FastMCP
    except (ImportError, ModuleNotFoundError):
        from mcp.server.mcpserver import MCPServer as FastMCP

    server = FastMCP("enterprise-adapters")
    tools_instance = tools or EnterpriseAdaptersMCPTools()

    @server.tool(name="health_check")
    def health_check() -> dict[str, object]:
        """Check enterprise adapters service status and registered sources."""
        return tools_instance.health_check()

    @server.tool(name="list_sources")
    def list_sources(tenant_id: str | None = None) -> dict[str, object]:
        """List available enterprise source adapters and their allowlisted resources."""
        return tools_instance.list_sources(tenant_id=tenant_id)

    @server.tool(name="fetch_document")
    def fetch_document(
        source: str,
        resource_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, object]:
        """Fetch sanitized document content from an approved enterprise source adapter."""
        return tools_instance.fetch_document(
            source=source,
            resource_id=resource_id,
            tenant_id=tenant_id,
        )

    return server


def main() -> None:
    """Run the Enterprise Adapters MCP server over standard I/O transport."""
    server = create_mcp_server()
    server.run()


if __name__ == "__main__":
    main()
