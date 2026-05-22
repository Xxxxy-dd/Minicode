from dataclasses import dataclass
from typing import Any

from minicode_agent.core.state import AgentPhase, AgentState
from minicode_agent.memory.store import MemoryKind, MemoryRecord, contains_secret


@dataclass(frozen=True)
class MemoryCandidate:
    kind: MemoryKind
    content: str
    confidence: float
    source_run_id: str
    tags: list[str]
    reason: str
    metadata: dict[str, Any]


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
