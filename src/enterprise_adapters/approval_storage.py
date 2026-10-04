# Copyright 2026 Invarcore Organization
# SPDX-License-Identifier: Apache-2.0

"""Persistent storage for approval requests and policy decisions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from enterprise_adapters.approvals import ApprovalRequest
from enterprise_adapters.policy import PolicyDecision


@dataclass(slots=True)
class ApprovalRecord:
    """Stored approval record."""

    approval_id: str
    action_name: str
    requested_by: str
    summary: str
    decision: str
    policy_reasons: list[str]
    created_at: str
    updated_at: str


class SQLiteApprovalStore:
    """Durable approval store backed by SQLite."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save(self, approval: ApprovalRequest, policy_decision: PolicyDecision) -> ApprovalRecord:
        now = datetime.now(UTC).isoformat()
        record = ApprovalRecord(
            approval_id=approval.approval_id,
            action_name=approval.action_name,
            requested_by=approval.requested_by,
            summary=approval.summary,
            decision=policy_decision.decision.value,
            policy_reasons=list(approval.policy_reasons),
            created_at=now,
            updated_at=now,
        )
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO approvals (
                    approval_id, action_name, requested_by, summary, decision, policy_reasons, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(approval_id) DO UPDATE SET
                    action_name=excluded.action_name,
                    requested_by=excluded.requested_by,
                    summary=excluded.summary,
                    decision=excluded.decision,
                    policy_reasons=excluded.policy_reasons,
                    updated_at=excluded.updated_at
                """,
                [
                    record.approval_id,
                    record.action_name,
                    record.requested_by,
                    record.summary,
                    record.decision,
                    json.dumps(record.policy_reasons),
                    record.created_at,
                    record.updated_at,
                ],
            )
        return record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT approval_id, action_name, requested_by, summary, decision, policy_reasons, created_at, updated_at
                FROM approvals
                WHERE approval_id = ?
                """,
                [approval_id],
            ).fetchone()
        if row is None:
            return None
        return ApprovalRecord(
            approval_id=row[0],
            action_name=row[1],
            requested_by=row[2],
            summary=row[3],
            decision=row[4],
            policy_reasons=list(json.loads(row[5])),
            created_at=row[6],
            updated_at=row[7],
        )

    def list_all(self) -> list[ApprovalRecord]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """
                SELECT approval_id, action_name, requested_by, summary, decision, policy_reasons, created_at, updated_at
                FROM approvals
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [
            ApprovalRecord(
                approval_id=row[0],
                action_name=row[1],
                requested_by=row[2],
                summary=row[3],
                decision=row[4],
                policy_reasons=list(json.loads(row[5])),
                created_at=row[6],
                updated_at=row[7],
            )
            for row in rows
        ]

    def _initialize(self) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    action_name TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    policy_reasons TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
