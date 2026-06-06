from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from minicode_agent.memory.evidence import memory_evidence_refs
from minicode_agent.memory.policy import (
    MemoryAdmissionPolicy,
    filter_status,
    normalize_memory_content,
    score_memory,
)
from minicode_agent.security.redaction import contains_secret, safe_payload


class MemoryKind(StrEnum):
    PROJECT = "project_memory"
    USER = "user_memory"
    PROCEDURE = "procedure_memory"
    FAILURE = "failure_memory"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    CONFLICT = "conflict"


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: MemoryKind
    content: str
    confidence: float = 0.5
    source_run_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tags: list[str] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: MemoryStatus = MemoryStatus.ACTIVE
    admission_reason: str | None = None

    @field_validator("content")
    @classmethod
    def require_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory content cannot be empty")
        return normalized

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("memory confidence must be between 0 and 1")
        return value


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve()
        self.jsonl_path = self.db_path.with_suffix(".jsonl")
        self.backend = "sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._init_db()
        except (ImportError, OSError):
            self.backend = "jsonl"

    def add(
        self,
        kind: MemoryKind | str,
        content: str,
        confidence: float = 0.5,
        source_run_id: str | None = None,
        tags: list[str] | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        admission_reason: str | None = None,
    ) -> tuple[MemoryRecord, bool]:
        reject_secret_field(content, "content")
        reject_secret_field(reason or "", "reason")
        for tag in tags or []:
            reject_secret_field(tag, "tag")
        safe_metadata = safe_payload(metadata or {})
        notes = MemoryAdmissionPolicy().conflict_notes(self.list(limit=200), MemoryKind(kind), content)
        if notes:
            safe_metadata["conflict_notes"] = notes
        record = MemoryRecord(
            kind=MemoryKind(kind),
            content=content,
            confidence=confidence,
            source_run_id=source_run_id,
            tags=tags or [],
            reason=reason,
            metadata=safe_metadata,
            status=MemoryStatus.CONFLICT if notes else MemoryStatus.ACTIVE,
            admission_reason=admission_reason or "accepted",
        )
        normalized = normalize_memory_content(record.content)
        existing = self.find_duplicate(record.kind, normalized)
        if existing is not None:
            return existing, False

        if self.backend == "jsonl":
            payload = record.model_dump()
            payload["normalized_content"] = normalized
            with self.jsonl_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return record, True

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_records (
                    id, kind, normalized_content, content, confidence,
                    source_run_id, created_at, updated_at, tags_json, reason, metadata_json,
                    status, admission_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.kind.value,
                    normalized,
                    record.content,
                    record.confidence,
                    record.source_run_id,
                    record.created_at,
                    record.updated_at,
                    json.dumps(record.tags, ensure_ascii=False),
                    record.reason,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.status.value,
                    record.admission_reason,
                ),
            )
        return record, True

    def list(
        self,
        kind: MemoryKind | str | None = None,
        limit: int = 50,
        status: MemoryStatus | str | None = None,
        include_stale: bool = True,
    ) -> list[MemoryRecord]:
        if self.backend == "jsonl":
            records = self._read_jsonl_records()
            if kind is not None:
                memory_kind = MemoryKind(kind)
                records = [record for record in records if record.kind == memory_kind]
            records = filter_status(records, status=status, include_stale=include_stale)
            records.sort(key=lambda record: (record.updated_at, record.created_at), reverse=True)
            return records[:limit]

        query = """
            SELECT id, kind, content, confidence, source_run_id, created_at, updated_at, tags_json, reason, metadata_json,
                   status, admission_reason
            FROM memory_records
        """
        params: list[Any] = []
        conditions: list[str] = []
        if kind is not None:
            conditions.append("kind = ?")
            params.append(MemoryKind(kind).value)
        if status is not None:
            conditions.append("status = ?")
            params.append(MemoryStatus(status).value)
        elif not include_stale:
            conditions.append("status != ?")
            params.append(MemoryStatus.STALE.value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search(
        self,
        query: str,
        limit: int = 8,
        kind: MemoryKind | str | None = None,
        status: MemoryStatus | str | None = None,
        tags: list[str] | None = None,
        include_stale: bool = False,
    ) -> list[MemoryRecord]:
        terms = {term for term in re.findall(r"[\w.-]+", query.lower()) if len(term) >= 3}
        records = self.list(kind=kind, status=status, include_stale=include_stale, limit=200)
        if tags:
            wanted = {tag.lower() for tag in tags}
            records = [record for record in records if wanted <= {tag.lower() for tag in record.tags}]
        if not terms:
            return records[:limit]

        scored: list[tuple[float, MemoryRecord]] = []
        for record in records:
            score, reason = score_memory(query, record)
            if reason != "no query match":
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [record for _, record in scored[:limit]]

    def recall(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        records = self.search(query, limit=limit)

        return [
            {
                "record": record,
                "reason": reason,
                "score": score,
                "evidence_refs": memory_evidence_refs(record),
            }
            for record in records
            for score, reason in [score_memory(query, record)]
        ]

    def delete(self, memory_id: str) -> bool:
        if self.backend == "jsonl":
            records = self._read_jsonl_records()
            kept = [record for record in records if record.id != memory_id]
            if len(kept) == len(records):
                return False
            with self.jsonl_path.open("w", encoding="utf-8") as file:
                for record in kept:
                    payload = record.model_dump()
                    payload["normalized_content"] = normalize_memory_content(record.content)
                    file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return True

        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM memory_records WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0

    def mark_status(self, memory_id: str, status: MemoryStatus | str, reason: str | None = None) -> bool:
        memory_status = MemoryStatus(status)
        if self.backend == "jsonl":
            records = self._read_jsonl_records()
            changed = False
            now = datetime.now(UTC).isoformat()
            with self.jsonl_path.open("w", encoding="utf-8") as file:
                for record in records:
                    if record.id == memory_id:
                        record.status = memory_status
                        record.updated_at = now
                        if reason:
                            record.metadata["status_reason"] = reason
                        changed = True
                    payload = record.model_dump()
                    payload["normalized_content"] = normalize_memory_content(record.content)
                    file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return changed

        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [memory_status.value, datetime.now(UTC).isoformat()]
        if reason:
            record = self.get(memory_id)
            if record is None:
                return False
            metadata = dict(record.metadata)
            metadata["status_reason"] = reason
            updates.append("metadata_json = ?")
            params.append(json.dumps(safe_payload(metadata), ensure_ascii=False))
        params.append(memory_id)
        with self._connect() as conn:
            cursor = conn.execute(f"UPDATE memory_records SET {', '.join(updates)} WHERE id = ?", tuple(params))
        return cursor.rowcount > 0

    def get(self, memory_id: str) -> MemoryRecord | None:
        if self.backend == "jsonl":
            return next((record for record in self._read_jsonl_records() if record.id == memory_id), None)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, kind, content, confidence, source_run_id, created_at, updated_at, tags_json, reason, metadata_json,
                       status, admission_reason
                FROM memory_records
                WHERE id = ?
                """,
                (memory_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def find_duplicate(self, kind: MemoryKind, normalized_content: str) -> MemoryRecord | None:
        if self.backend == "jsonl":
            for record in self._read_jsonl_records():
                if record.kind == kind and normalize_memory_content(record.content) == normalized_content:
                    return record
            return None

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, kind, content, confidence, source_run_id, created_at, updated_at, tags_json, reason, metadata_json,
                       status, admission_reason
                FROM memory_records
                WHERE kind = ? AND normalized_content = ?
                """,
                (kind.value, normalized_content),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    normalized_content TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(kind, normalized_content)
                )
                """
            )
            self._ensure_column(conn, "reason", "TEXT")
            self._ensure_column(conn, "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(conn, "admission_reason", "TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_records_kind ON memory_records(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_records_status ON memory_records(status)")

    def _connect(self):
        import sqlite3

        return sqlite3.connect(self.db_path)

    def _row_to_record(self, row: tuple[Any, ...]) -> MemoryRecord:
        return MemoryRecord(
            id=row[0],
            kind=MemoryKind(row[1]),
            content=row[2],
            confidence=row[3],
            source_run_id=row[4],
            created_at=row[5],
            updated_at=row[6],
            tags=json.loads(row[7]),
            reason=row[8] if len(row) > 8 else None,
            metadata=json.loads(row[9]) if len(row) > 9 else {},
            status=MemoryStatus(row[10]) if len(row) > 10 and row[10] else MemoryStatus.ACTIVE,
            admission_reason=row[11] if len(row) > 11 else None,
        )

    def _ensure_column(self, conn, name: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_records)").fetchall()}
        if name not in columns:
            conn.execute(f"ALTER TABLE memory_records ADD COLUMN {name} {definition}")

    def _read_jsonl_records(self) -> list[MemoryRecord]:
        if not self.jsonl_path.exists():
            return []
        records: list[MemoryRecord] = []
        for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            payload.pop("normalized_content", None)
            records.append(MemoryRecord.model_validate(payload))
        return records


def reject_secret_field(value: str, field: str) -> None:
    if contains_secret(value):
        raise ValueError(f"memory {field} appears to contain a secret")


def looks_contradictory(left: str, right: str) -> bool:
    if not shared_significant_terms(left, right):
        return False
    negations = ("not ", "no ", "never ", "不要", "不能", "不使用", "禁用")
    return any(token in left for token in negations) != any(token in right for token in negations)


def shared_significant_terms(left: str, right: str) -> bool:
    left_terms = {term for term in re.findall(r"[\w.-]+", left) if len(term) >= 4}
    right_terms = {term for term in re.findall(r"[\w.-]+", right) if len(term) >= 4}
    return bool(left_terms & right_terms)


def default_memory_db_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / ".minicode" / "memory" / "memory.db"
