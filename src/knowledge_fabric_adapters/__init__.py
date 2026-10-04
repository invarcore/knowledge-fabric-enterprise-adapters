# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""
knowledge-fabric-adapters — public adapter contracts for the knowledge-fabric ecosystem.

This package contains the Protocol interfaces, data models, and base classes
needed to write custom source and runtime adapters that integrate with
knowledge-fabric and intent-fabric.

The private ``enterprise-adapters`` package contains production implementations
(Confluence, SharePoint, ServiceNow, etc.) that depend on these contracts.
Community-written adapters should depend on this package instead.

Installation:
    pip install knowledge-fabric-adapters

Quick-start for adapter authors:
    from knowledge_fabric_adapters import KnowledgeSourceAdapter, ReadOnlyResource, AdapterContext
"""

from knowledge_fabric_adapters.connectors import (
    ConfluenceSourceAdapter,
    GoogleDriveSourceAdapter,
    JiraSourceAdapter,
    NotionSourceAdapter,
)
from knowledge_fabric_adapters.contracts import (
    AdapterContext,
    KnowledgeSourceAdapter,
    ReadOnlyResource,
    RuntimeActionAdapter,
    RuntimeMetadataAdapter,
    verify_approval_signature,
)
from knowledge_fabric_adapters.security import (
    RedactingLoggingFilter,
    sanitize_exception,
    sanitize_log_message,
)

__all__ = [
    "AdapterContext",
    "KnowledgeSourceAdapter",
    "ReadOnlyResource",
    "RuntimeActionAdapter",
    "RuntimeMetadataAdapter",
    "verify_approval_signature",
    "ConfluenceSourceAdapter",
    "NotionSourceAdapter",
    "GoogleDriveSourceAdapter",
    "JiraSourceAdapter",
    "sanitize_log_message",
    "sanitize_exception",
    "RedactingLoggingFilter",
]

__version__ = "0.1.0"
