# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Security and secret-scrubbing utilities for enterprise adapters.

Prevents credentials, API keys, Bearer tokens, and Authorization headers
from accidentally leaking into logs, audit traces, or exception outputs.
"""

from __future__ import annotations

import logging
import os
import re

from enterprise_adapters.content_sanitizer import sanitize_document_content

__all__ = [
    "sanitize_log_message",
    "sanitize_exception",
    "RedactingLoggingFilter",
    "sanitize_document_content",
]

_BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE)
_BASIC_PATTERN = re.compile(r"(Basic\s+)[A-Za-z0-9+/=]{8,}", re.IGNORECASE)
_QUERY_PARAM_PATTERN = re.compile(
    r"((?:api[_-]?key|token|secret|password|access[_-]?token|auth)=)[^&\s'\"]+",
    re.IGNORECASE,
)
_HEADER_AUTH_PATTERN = re.compile(r"('authorization':\s*')[^']+(')", re.IGNORECASE)


def sanitize_log_message(message: str) -> str:
    """Scrub sensitive credentials, tokens, and authorization strings from a message.

    Can be toggled via ADAPTERS_REDACT_SECRETS=false for low-level local debugging.
    """
    if not isinstance(message, str):
        return str(message)
    if os.environ.get("ADAPTERS_REDACT_SECRETS", "true").lower() in ("false", "0", "off"):
        return message

    scrubbed = _BEARER_PATTERN.sub(r"\1[REDACTED]", message)
    scrubbed = _BASIC_PATTERN.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _QUERY_PARAM_PATTERN.sub(r"\1[REDACTED]", scrubbed)
    scrubbed = _HEADER_AUTH_PATTERN.sub(r"\1[REDACTED]\2", scrubbed)
    return sanitize_document_content(scrubbed)


def sanitize_exception(exc: Exception) -> str:
    """Produce a safe, redacted exception representation."""
    return sanitize_log_message(str(exc))


class RedactingLoggingFilter(logging.Filter):
    """Logging filter that automatically redacts secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_log_message(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: sanitize_log_message(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_log_message(v) if isinstance(v, str) else v
                    for v in record.args
                )
        return True
