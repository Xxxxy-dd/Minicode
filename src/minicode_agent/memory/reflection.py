import json
from dataclasses import dataclass
from typing import Any, Protocol

from minicode_agent.core.state import AgentPhase, AgentState
from minicode_agent.memory.store import MemoryKind, MemoryRecord, contains_secret


@dataclass(frozen=True)
class ReflectionModelMessage:
    role: str
    content: str


class ReflectionModelClient(Protocol):
    def complete(self, messages: list[ReflectionModelMessage]):
        """Return an object with a string content attribute."""


@dataclass(frozen=True)
class MemoryCandidate:
    kind: MemoryKind
    content: str
    confidence: float
    source_run_id: str
    tags: list[str]
    reason: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MemoryReflectionResult:
    summary: str | None
    candidates: list[MemoryCandidate]
    filtered_count: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None


class DeterministicReflectionEngine:
    """Rule-based memory candidate generator for the local Memory v1."""

    def generate(self, state: AgentState, observations: list[dict[str, Any]]) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        touched = unique_strings(state.files_touched)
        for path in touched[:3]:
            candidates.append(
                MemoryCandidate(
                    kind=MemoryKind.PROJECT,
                    content=f"Relevant file for task '{state.user_goal}': {path}",
                    confidence=0.6,
                    source_run_id=state.run_id,
                    tags=["reflection", "file"],
                    reason="agent touched a file while completing the run",
                    metadata={"rule": "touched_file", "path": path},
                )
            )

        if state.current_phase == AgentPhase.DONE and state.metrics.tool_calls > 0:
            tools = unique_strings(
                str(observation.get("payload", {}).get("tool") or observation.get("tool") or "")
                for observation in observations
            )
            tool_text = ", ".join(tool for tool in tools if tool) or "registered tools"
            candidates.append(
                MemoryCandidate(
                    kind=MemoryKind.PROCEDURE,
                    content=f"Successful local run pattern for '{state.user_goal}': use {tool_text}, then verify before finishing.",
                    confidence=0.55,
                    source_run_id=state.run_id,
                    tags=["reflection", "procedure"],
                    reason="run finished successfully with tool calls",
                    metadata={"rule": "successful_tool_sequence", "tools": tools},
                )
            )

        for attempt in unique_strings(state.task_state.failed_attempts)[-3:]:
            candidates.append(
                MemoryCandidate(
                    kind=MemoryKind.FAILURE,
                    content=f"Failed attempt during '{state.user_goal}': {attempt}",
                    confidence=0.7,
                    source_run_id=state.run_id,
                    tags=["reflection", "failure"],
                    reason="failed attempt recorded in task state",
                    metadata={"rule": "failed_attempt"},
                )
            )
        return candidates

    def summarize(self, state: AgentState, candidates: list[MemoryCandidate]) -> str | None:
        if not candidates:
            return None
        pieces = [candidate.content for candidate in candidates[:2]]
        return truncate_summary(
            f"Remember {len(candidates)} useful facts from '{state.user_goal}': " + "; ".join(pieces)
        )

    def admit(self, candidate: MemoryCandidate) -> tuple[MemoryRecord | None, str]:
        if candidate.confidence < 0.5:
            return None, "confidence below admission threshold"
        if contains_secret(candidate.content):
            return None, "candidate appears to contain a secret"
        return (
            MemoryRecord(
                kind=candidate.kind,
                content=candidate.content,
                confidence=candidate.confidence,
                source_run_id=candidate.source_run_id,
                tags=candidate.tags,
                reason=candidate.reason,
                metadata=candidate.metadata,
            ),
            "accepted",
        )


class LLMReflectionEngine:
    """Model-backed memory candidate generator with deterministic admission."""

    def __init__(self, model_client: ReflectionModelClient, fallback: DeterministicReflectionEngine | None = None) -> None:
        self.model_client = model_client
        self.fallback = fallback or DeterministicReflectionEngine()

    def generate(self, state: AgentState, observations: list[dict[str, Any]]) -> MemoryReflectionResult:
        messages = [
            ReflectionModelMessage(
                role="system",
                content=(
                    "You generate durable memory candidates and a short summary for MiniCode Agent. "
                    "Return only JSON with fields: summary and memories. "
                    "summary should be a short durable note for future runs. "
                    "Each memory must have: keep, kind, content, confidence, tags, reason. "
                    "Allowed kinds: project_memory, user_memory, procedure_memory, failure_memory. "
                    "Set keep=false for noisy, duplicate, or low-value items. Do not include secrets or raw credentials."
                ),
            ),
            ReflectionModelMessage(
                role="user",
                content=json.dumps(
                    {
                        "run_id": state.run_id,
                        "goal": state.user_goal,
                        "final_phase": state.current_phase.value,
                        "files_touched": state.files_touched,
                        "failed_attempts": state.task_state.failed_attempts[-5:],
                        "decisions": state.task_state.decisions[-5:],
                        "metrics": state.metrics.model_dump(),
                        "observations": compact_observations(observations),
                        "existing_history_summary": state.task_state.history_summary,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            ),
        ]
        response = self.model_client.complete(messages)
        return parse_llm_memory_response(response.content, state.run_id)

    def generate_with_fallback(self, state: AgentState, observations: list[dict[str, Any]]) -> MemoryReflectionResult:
        try:
            return self.generate(state, observations)
        except Exception as exc:
            candidates = self.fallback.generate(state, observations)
            return MemoryReflectionResult(
                summary=self.fallback.summarize(state, candidates),
                candidates=candidates,
                filtered_count=0,
                fallback_used=True,
                fallback_reason=str(exc),
            )


def unique_strings(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def parse_llm_memory_candidates(content: str, source_run_id: str) -> list[MemoryCandidate]:
    return parse_llm_memory_response(content, source_run_id).candidates


def parse_llm_memory_response(content: str, source_run_id: str) -> MemoryReflectionResult:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM memory response must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM memory response must be a JSON object.")
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("LLM memory response field 'summary' must be a string or null.")
    memories = payload.get("memories", [])
    if not isinstance(memories, list):
        raise ValueError("LLM memory response field 'memories' must be a list.")

    candidates: list[MemoryCandidate] = []
    filtered_count = 0
    for index, item in enumerate(memories):
        if not isinstance(item, dict):
            raise ValueError(f"LLM memory item {index} must be an object.")
        keep = item.get("keep", True)
        if not isinstance(keep, bool):
            raise ValueError(f"LLM memory item {index} keep must be a boolean.")
        if not keep:
            filtered_count += 1
            continue
        kind = parse_memory_kind(item.get("kind"), index)
        content_text = required_text(item.get("content"), f"memories[{index}].content")
        confidence = parse_confidence(item.get("confidence", 0.5), index)
        tags = parse_tags(item.get("tags", []), index)
        reason = required_text(item.get("reason"), f"memories[{index}].reason")
        candidates.append(
            MemoryCandidate(
                kind=kind,
                content=content_text,
                confidence=confidence,
                source_run_id=source_run_id,
                tags=["llm_reflection", *tags],
                reason=reason,
                metadata={"generator": "llm", "index": index},
            )
        )
    return MemoryReflectionResult(
        summary=truncate_summary(summary) if summary else None,
        candidates=candidates,
        filtered_count=filtered_count,
    )


def parse_memory_kind(value: Any, index: int) -> MemoryKind:
    if not isinstance(value, str):
        raise ValueError(f"LLM memory item {index} kind must be a string.")
    try:
        return MemoryKind(value)
    except ValueError as exc:
        raise ValueError(f"LLM memory item {index} has unknown kind: {value}") from exc


def parse_confidence(value: Any, index: int) -> float:
    if not isinstance(value, int | float):
        raise ValueError(f"LLM memory item {index} confidence must be a number.")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise ValueError(f"LLM memory item {index} confidence must be between 0 and 1.")
    return confidence


def parse_tags(value: Any, index: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"LLM memory item {index} tags must be a list.")
    return [str(tag).strip() for tag in value if str(tag).strip()]


def required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"LLM memory response field '{field}' must be a non-empty string.")
    return value.strip()


def compact_observations(observations: list[dict[str, Any]], limit: int = 8, max_chars: int = 500) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for observation in observations[-limit:]:
        payload = observation.get("payload", observation)
        text = str(payload.get("result") or payload.get("output") or payload.get("error") or "")
        compacted.append(
            {
                "event": observation.get("event"),
                "tool": payload.get("tool"),
                "ok": payload.get("ok"),
                "text": text[:max_chars],
            }
        )
    return compacted


def truncate_summary(value: str | None, max_chars: int = 240) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
