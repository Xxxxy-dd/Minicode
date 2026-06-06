from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from minicode_agent.memory.evidence import candidate_evidence_refs
from minicode_agent.security.redaction import contains_secret, safe_payload


class MemoryAdmissionReasonCode(StrEnum):
    ACCEPTED = "accepted"
    LOW_CONFIDENCE = "low_confidence"
    SECRET_DETECTED = "secret_detected"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    STALE = "stale"


@dataclass(frozen=True)
class MemoryAdmissionDecision:
    accepted: bool
    reason_code: MemoryAdmissionReasonCode
    reason: str
    evidence_refs: list[dict[str, Any]]
    record: Any | None = None


class MemoryAdmissionPolicy:
    """Deterministic admission policy separated from memory storage."""

    def __init__(self, min_confidence: float = 0.5, failure_min_confidence: float = 0.45) -> None:
        self.min_confidence = min_confidence
        self.failure_min_confidence = failure_min_confidence

    def evaluate(self, candidate) -> MemoryAdmissionDecision:
        evidence_refs = candidate_evidence_refs(candidate)
        threshold = self.failure_min_confidence if memory_kind_value(candidate.kind) == "failure_memory" else self.min_confidence
        if candidate.confidence < threshold:
            return MemoryAdmissionDecision(
                accepted=False,
                reason_code=MemoryAdmissionReasonCode.LOW_CONFIDENCE,
                reason="confidence below admission threshold",
                evidence_refs=evidence_refs,
            )
        if contains_secret(candidate.content):
            return MemoryAdmissionDecision(
                accepted=False,
                reason_code=MemoryAdmissionReasonCode.SECRET_DETECTED,
                reason="candidate appears to contain a secret",
                evidence_refs=evidence_refs,
            )
        from minicode_agent.memory.store import MemoryRecord

        metadata = safe_payload(
            {
                **candidate.metadata,
                "admission_policy": "default",
                "admission_reason_code": MemoryAdmissionReasonCode.ACCEPTED.value,
            }
        )
        return MemoryAdmissionDecision(
            accepted=True,
            reason_code=MemoryAdmissionReasonCode.ACCEPTED,
            reason="accepted",
            evidence_refs=evidence_refs,
            record=MemoryRecord(
                kind=candidate.kind,
                content=candidate.content,
                confidence=candidate.confidence,
                source_run_id=candidate.source_run_id,
                tags=candidate.tags,
                reason=candidate.reason,
                metadata=metadata,
                admission_reason="accepted",
            ),
        )

    def conflict_notes(self, existing_records: list[Any], kind: Any, content: str) -> list[str]:
        return conflict_notes(existing_records, kind, content)

    def recall_allowed(self, record: Any, *, include_stale: bool) -> bool:
        return include_stale or memory_status_value(record.status) != "stale"

    def score(self, query: str, record: Any) -> tuple[float, str]:
        return score_memory(query, record)


def filter_status(
    records: list[Any],
    *,
    status: Any | None,
    include_stale: bool,
) -> list[Any]:
    if status is not None:
        wanted = memory_status_value(status)
        return [record for record in records if memory_status_value(record.status) == wanted]
    if not include_stale:
        return [record for record in records if memory_status_value(record.status) != "stale"]
    return records


def score_memory(query: str, record: Any) -> tuple[float, str]:
    terms = {term for term in re.findall(r"[\w.-]+", query.lower()) if len(term) >= 3}
    if not terms:
        return record.confidence, "recent active memory"
    content_hits = sum(1 for term in terms if term in record.content.lower())
    tag_hits = sum(1 for term in terms if term in " ".join(record.tags).lower())
    kind_hits = sum(1 for term in terms if term in memory_kind_value(record.kind))
    hit_score = content_hits + (tag_hits * 2) + (kind_hits * 1.5)
    matched = [
        term
        for term in sorted(terms)
        if term in record.content.lower() or term in " ".join(record.tags).lower() or term in memory_kind_value(record.kind)
    ]
    if not hit_score:
        return record.confidence, "no query match"
    return hit_score + record.confidence, f"matched query terms: {', '.join(matched[:5])}"


def conflict_notes(existing_records: list[Any], kind: Any, content: str) -> list[str]:
    normalized = normalize_memory_content(content)
    notes: list[str] = []
    wanted_kind = memory_kind_value(kind)
    for record in existing_records:
        if memory_kind_value(record.kind) != wanted_kind or memory_status_value(record.status) != "active":
            continue
        old = normalize_memory_content(record.content)
        if old == normalized:
            continue
        if looks_contradictory(old, normalized):
            notes.append(f"possible conflict with {record.id}")
    return notes


def looks_contradictory(left: str, right: str) -> bool:
    if not shared_significant_terms(left, right):
        return False
    negations = ("not ", "no ", "never ", "不要", "不能", "不使用", "禁用")
    return any(token in left for token in negations) != any(token in right for token in negations)


def shared_significant_terms(left: str, right: str) -> bool:
    left_terms = {term for term in re.findall(r"[\w.-]+", left) if len(term) >= 4}
    right_terms = {term for term in re.findall(r"[\w.-]+", right) if len(term) >= 4}
    return bool(left_terms & right_terms)


def normalize_memory_content(content: str) -> str:
    return " ".join(content.strip().casefold().split())


def memory_kind_value(kind: Any) -> str:
    return str(getattr(kind, "value", kind))


def memory_status_value(status: Any) -> str:
    return str(getattr(status, "value", status))
