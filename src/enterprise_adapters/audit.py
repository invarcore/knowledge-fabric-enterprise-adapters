# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Audit trail implementations — in-memory (dev) and SQLite-backed (production)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass(slots=True)
class AuditEvent:
    """An audit record for private adapter actions."""

    event_type: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, object] = field(default_factory=dict)


class InMemoryAuditTrail:
    """Simple audit sink for tests and local dev. Events are lost on restart."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, event_type: str, **details: object) -> AuditEvent:
        event = AuditEvent(event_type=event_type, details=details)
        self._events.append(event)
        return event

    def events(self) -> list[AuditEvent]:
        return list(self._events)


class SQLiteAuditTrail:
    """Durable audit trail backed by SQLite. Safe for single-process production use.

    Set AUDIT_DB_PATH env var or pass database_path explicitly.

    Example:
        trail = SQLiteAuditTrail("/var/lib/enterprise-adapters/audit.sqlite3")
        trail.record("execution.completed", action_name="create_ticket", approval_id="abc")
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def record(self, event_type: str, **details: object) -> AuditEvent:
        event = AuditEvent(event_type=event_type, details=details)
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                "INSERT INTO audit_events (event_type, occurred_at, details) VALUES (?, ?, ?)",
                [event_type, event.occurred_at.isoformat(), json.dumps(details, default=str)],
            )
        return event

    def events(self) -> list[AuditEvent]:
        with sqlite3.connect(self._database_path) as conn:
            rows = conn.execute(
                "SELECT event_type, occurred_at, details FROM audit_events ORDER BY occurred_at ASC"
            ).fetchall()
        return [
            AuditEvent(
                event_type=row[0],
                occurred_at=datetime.fromisoformat(row[1]),
                details=json.loads(row[2]),
            )
            for row in rows
        ]

    def _initialize(self) -> None:
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}'
                )
                """
            )


def build_audit_trail(database_path: str | Path | None = None) -> SQLiteAuditTrail | InMemoryAuditTrail:
    """Factory: returns SQLiteAuditTrail when a path is given, InMemory otherwise.

    Usage:
        import os
        trail = build_audit_trail(os.environ.get("AUDIT_DB_PATH"))
    """
    import os
    resolved = database_path or os.environ.get("AUDIT_DB_PATH", "")
    if resolved:
        return SQLiteAuditTrail(resolved)
    return InMemoryAuditTrail()
