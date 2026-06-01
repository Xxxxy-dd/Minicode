from __future__ import annotations

from pathlib import Path
from typing import Any

from minicode_agent.cli.preview_renderer import render_preview_text
from minicode_agent.memory import MemoryRecord
from minicode_agent.skills import SkillRouteResult
from minicode_agent.trace import TraceEvent


def render_memory_record(record: MemoryRecord) -> str:
    lines = [
        (
            f"- id={record.id} kind={record.kind.value} status={record.status.value} "
            f"confidence={record.confidence:.2f} source={record.source_run_id or '(none)'}"
        ),
        f"  tags={', '.join(record.tags) or '(none)'}",
        f"  reason={record.reason or '(none)'}",
        f"  admission={record.admission_reason or '(none)'}",
    ]
    metadata = summarize_metadata(record.metadata)
    if metadata:
        lines.append(f"  metadata={metadata}")
    lines.append(f"  content={record.content}")
    return "\n".join(lines)


def render_memory_summary(records: list[MemoryRecord], *, backend: str | None = None, path: Path | None = None) -> str:
    lines: list[str] = []
    if backend and path:
        lines.append(f"memory_backend: {backend} ({path})")
    if not records:
        lines.append("(no memories)")
        return "\n".join(lines)
    for record in records:
        lines.append(render_memory_record(record))
    return "\n".join(lines)


def render_skill_route_summary(result: SkillRouteResult) -> str:
    lines = [result.debug_summary]
    if not result.candidates:
        lines.append("No matching skills.")
        return "\n".join(line for line in lines if line)
    for candidate in result.candidates[:5]:
        selected = "*" if candidate.name in result.selected else "-"
        reasons = "; ".join(candidate.reasons)
        lines.append(f"{selected} {candidate.name} score={candidate.score} {reasons}".rstrip())
    if result.rerank_used:
        suffix = " fallback" if result.rerank_fallback else ""
        lines.append(f"rerank{suffix}: {result.rerank_reason or 'completed'}")
    elif result.rerank_skipped_reason:
        lines.append(f"rerank skipped: {result.rerank_skipped_reason}")
    return "\n".join(line for line in lines if line)


def render_trace_summary(events: list[TraceEvent], *, backend: str | None = None, path: Path | None = None, limit: int = 8) -> str:
    lines: list[str] = []
    if backend and path:
        lines.append(f"trace_backend: {backend} ({path})")
    if not events:
        lines.append("(no trace events)")
        return "\n".join(lines)
    for event in events[-limit:]:
        tool = event.payload.get("tool") or event.payload.get("metadata", {}).get("tool", "")
        ok = event.payload.get("ok", "")
        reason = event.payload.get("reason") or event.payload.get("error") or event.payload.get("metadata", {}).get("permission_reason", "")
        pieces = [event.event_type]
        if tool:
            pieces.append(f"tool={tool}")
        if ok != "":
            pieces.append(f"ok={ok}")
        if reason:
            pieces.append(f"reason={str(reason)[:100]}")
        lines.append("- " + " ".join(str(piece) for piece in pieces))
    return "\n".join(lines)


def render_diff_summary(preview: dict[str, Any] | None) -> str:
    if not isinstance(preview, dict) or not preview:
        return "(no diff preview recorded)"
    return render_preview_text(preview, heading="Recent diff")


def render_tool_summary(tool: str | None, ok: bool | None, result: str | None = None) -> str:
    if not tool:
        return "(no tool call recorded)"
    if ok is True:
        status = "ok"
    elif ok is False:
        status = "failed"
    else:
        status = "unknown"
    pieces = [f"{tool} {status}"]
    if result:
        pieces.append(" ".join(result.split())[:120])
    return " | ".join(pieces)


def summarize_metadata(metadata: dict) -> str:
    if not metadata:
        return ""
    pieces = []
    for key in ("rule", "path", "status_reason", "conflict_notes"):
        value = metadata.get(key)
        if value:
            pieces.append(f"{key}={value}")
    if not pieces:
        pieces = [f"{key}={value}" for key, value in list(metadata.items())[:2]]
    text = "; ".join(str(piece) for piece in pieces)
    return text[:120]
