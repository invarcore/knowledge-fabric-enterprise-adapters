"""Content scrubbing and redaction for enterprise document adapters.

Prevents credentials, API keys, private keys, database passwords, and Bearer tokens
from being ingested into downstream vector stores or knowledge repositories.
"""

from __future__ import annotations

import os
import re

_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9_ -]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9_ -]*PRIVATE KEY-----",
    re.IGNORECASE,
)
_AWS_KEY_PATTERN = re.compile(r"\b(AKIA[0-9A-Z]{16})\b")
_API_KEY_PATTERN = re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,})\b")
_GITHUB_TOKEN_PATTERN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{36,})\b")
_SLACK_TOKEN_PATTERN = re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})\b")
_BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE)
_BASIC_PATTERN = re.compile(r"(Basic\s+)[A-Za-z0-9+/=]{8,}", re.IGNORECASE)
_CONN_STRING_PATTERN = re.compile(
    r"((?:postgres|postgresql|mysql|mongodb|redis)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)
_QUERY_PARAM_PATTERN = re.compile(
    r"((?:api[_-]?key|token|secret|password|access[_-]?token|auth)=)[^&\s'\"]+",
    re.IGNORECASE,
)


def sanitize_document_content(content: str) -> str:
    """Scrub sensitive credentials, private keys, and API tokens from document text.

    Can be disabled via ADAPTERS_REDACT_SECRETS=false for low-level local debugging.
    """
    if not isinstance(content, str) or not content:
        return "" if content is None else str(content)

    if os.environ.get("ADAPTERS_REDACT_SECRETS", "true").lower() in ("false", "0", "off"):
        return content

    scrubbed = _PRIVATE_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", content)
    scrubbed = _AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", scrubbed)
    scrubbed = _API_KEY_PATTERN.sub("[REDACTED_API_KEY]", scrubbed)
    scrubbed = _GITHUB_TOKEN_PATTERN.sub("[REDACTED_GITHUB_TOKEN]", scrubbed)
    scrubbed = _SLACK_TOKEN_PATTERN.sub("[REDACTED_SLACK_TOKEN]", scrubbed)
    scrubbed = _BEARER_PATTERN.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _BASIC_PATTERN.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _CONN_STRING_PATTERN.sub(r"\1[REDACTED]\3", scrubbed)
    scrubbed = _QUERY_PARAM_PATTERN.sub(r"\1[REDACTED]", scrubbed)

    return scrubbed
