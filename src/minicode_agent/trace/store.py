import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from minicode_agent.security.redaction import safe_payload as safe_trace_payload


class TraceEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    event_type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve()
        self.jsonl_path = self.db_path.with_suffix(".jsonl")
        self.backend = "sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._init_db()
        except ImportError:
            self.backend = "jsonl"

    @property
    def storage_path(self) -> Path:
        return self.jsonl_path if self.backend == "jsonl" else self.db_path

    def append(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> TraceEvent:
        event = TraceEvent(run_id=run_id, event_type=event_type, payload=safe_trace_payload(payload or {}))
        if self.backend == "jsonl":
            with self.jsonl_path.open("a", encoding="utf-8") as file:
                file.write(event.model_dump_json() + "\n")
            return event
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trace_events (id, run_id, event_type, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event.id, event.run_id, event.event_type, event.timestamp, json.dumps(event.payload, ensure_ascii=False)),
            )
        return event

    def list_events(self, run_id: str | None = None) -> list[TraceEvent]:
        if self.backend == "jsonl":
            if not self.jsonl_path.exists():
                return []
            events = [
                TraceEvent.model_validate_json(line)
                for line in self.jsonl_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if run_id is not None:
                events = [event for event in events if event.run_id == run_id]
            return events

        query = "SELECT id, run_id, event_type, timestamp, payload_json FROM trace_events"
        params: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY timestamp, rowid"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            TraceEvent(
                id=row[0],
                run_id=row[1],
                event_type=row[2],
                timestamp=row[3],
                payload=json.loads(row[4]),
            )
            for row in rows
        ]

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_events_run_id ON trace_events(run_id)")

    def _connect(self):
        import sqlite3

        return sqlite3.connect(self.db_path)


def default_trace_db_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / ".minicode" / "traces" / "trace.db"
