# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Structured logging helpers for private adapters."""

from __future__ import annotations

import json
import logging
from typing import Any


class JsonLineFormatter(logging.Formatter):
    """Render log records as compact JSON lines."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "structured", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, sort_keys=True)


def build_structured_logger(name: str = "enterprise_adapters", level: int = logging.INFO) -> logging.Logger:
    """Create a logger configured for JSON line output."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_structured_event(logger: logging.Logger, event_type: str, **fields: Any) -> None:
    """Emit a structured event with a stable schema."""
    logger.info(event_type, extra={"structured": {"event_type": event_type, **fields}})
