from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class MemoryKind(StrEnum):
    PROJECT = "project_memory"
    USER = "user_memory"
    PROCEDURE = "procedure_memory"
    FAILURE = "failure_memory"


SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
)


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
    ) -> tuple[MemoryRecord, bool]:
        if contains_secret(content):
            raise ValueError("memory content appears to contain a secret")
        record = MemoryRecord(
            kind=MemoryKind(kind),
            content=content,
            confidence=confidence,
            source_run_id=source_run_id,
            tags=tags or [],
            reason=reason,
            metadata=metadata or {},
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
                    source_run_id, created_at, updated_at, tags_json, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
        return record, True

    def list(self, kind: MemoryKind | str | None = None, limit: int = 50) -> list[MemoryRecord]:
        if self.backend == "jsonl":
            records = self._read_jsonl_records()
            if kind is not None:
                memory_kind = MemoryKind(kind)
                records = [record for record in records if record.kind == memory_kind]
            records.sort(key=lambda record: (record.updated_at, record.created_at), reverse=True)
            return records[:limit]

        query = """
            SELECT id, kind, content, confidence, source_run_id, created_at, updated_at, tags_json, reason, metadata_json
            FROM memory_records
        """
        params: list[Any] = []
        if kind is not None:
            query += " WHERE kind = ?"
            params.append(MemoryKind(kind).value)
        query += " ORDER BY updated_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search(self, query: str, limit: int = 8) -> list[MemoryRecord]:
        terms = {term for term in re.findall(r"[\w.-]+", query.lower()) if len(term) >= 3}
        records = self.list(limit=200)
        if not terms:
            return records[:limit]

        scored: list[tuple[float, MemoryRecord]] = []
        for record in records:
            haystack = f"{record.kind.value} {record.content} {' '.join(record.tags)}".lower()
            content_hits = sum(1 for term in terms if term in record.content.lower())
            tag_hits = sum(1 for term in terms if term in " ".join(record.tags).lower())
            kind_hits = sum(1 for term in terms if term in record.kind.value)
            hit_score = content_hits + (tag_hits * 2) + (kind_hits * 1.5)
            if hit_score:
                score = hit_score + record.confidence
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [record for _, record in scored[:limit]]

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

    def find_duplicate(self, kind: MemoryKind, normalized_content: str) -> MemoryRecord | None:
        if self.backend == "jsonl":
            for record in self._read_jsonl_records():
                if record.kind == kind and normalize_memory_content(record.content) == normalized_content:
                    return record
            return None

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, kind, content, confidence, source_run_id, created_at, updated_at, tags_json, reason, metadata_json
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_records_kind ON memory_records(kind)")

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


def normalize_memory_content(content: str) -> str:
    return " ".join(content.strip().casefold().split())


def contains_secret(content: str) -> bool:
    return any(pattern.search(content) for pattern in SECRET_PATTERNS)


def default_memory_db_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / ".minicode" / "memory" / "memory.db"
