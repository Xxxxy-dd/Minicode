from __future__ import annotations

from typing import Any


def candidate_evidence_refs(candidate) -> list[dict[str, Any]]:
    refs = [{"type": "run", "id": candidate.source_run_id}]
    refs.extend(metadata_evidence_refs(candidate.metadata))
    return dedupe_refs(refs)


def memory_evidence_refs(record) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if record.source_run_id:
        refs.append({"type": "run", "id": record.source_run_id})
    refs.extend(metadata_evidence_refs(record.metadata))
    return dedupe_refs(refs)


def metadata_evidence_refs(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return []
    refs: list[dict[str, Any]] = []
    path = metadata.get("path")
    if path:
        refs.append({"type": "file", "path": str(path)})
    rule = metadata.get("rule")
    if rule:
        refs.append({"type": "rule", "id": str(rule)})
    tool = metadata.get("tool")
    if tool:
        refs.append({"type": "tool", "name": str(tool)})
    trace_event_id = metadata.get("trace_event_id")
    if trace_event_id:
        refs.append({"type": "trace_event", "id": str(trace_event_id)})
    return refs


def dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for ref in refs:
        key = repr(sorted(ref.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped
