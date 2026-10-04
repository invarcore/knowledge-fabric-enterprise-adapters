# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Ready-to-use standard enterprise source connectors."""

from knowledge_fabric_adapters.connectors.confluence import ConfluenceSourceAdapter
from knowledge_fabric_adapters.connectors.google_drive import GoogleDriveSourceAdapter
from knowledge_fabric_adapters.connectors.jira import JiraSourceAdapter
from knowledge_fabric_adapters.connectors.notion import NotionSourceAdapter

__all__ = [
    "ConfluenceSourceAdapter",
    "NotionSourceAdapter",
    "GoogleDriveSourceAdapter",
    "JiraSourceAdapter",
]
