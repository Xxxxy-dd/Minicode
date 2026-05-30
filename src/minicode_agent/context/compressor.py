from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from minicode_agent.core.state import TaskState
from minicode_agent.tools.types import ToolStateEffect


class CompressionResult(BaseModel):
    task_state: TaskState
    input_chars: int
    output_chars: int
    ratio: float
    summary: str
    fallback_used: bool = False
    compressed_observations: int = 0
    compressed_observation_ids: list[str] = Field(default_factory=list)
    compressed_turns: list[int] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class TaskStateCompressor:
    """Deterministic context compressor that preserves structured task state."""

    def __init__(self, max_summary_chars: int = 1200, tool_effects: dict[str, set[ToolStateEffect]] | None = None) -> None:
        self.max_summary_chars = max_summary_chars
        self.tool_effects = tool_effects or {}

    def compress(
        self,
        task_state: TaskState,
        observations: list[dict[str, Any]],
    ) -> CompressionResult:
        input_text = "\n".join(observation_text(observation) for observation in observations)
        input_chars = len(input_text)
        summary_parts = []
        known_facts = list(task_state.known_facts)
        failed_attempts = list(task_state.failed_attempts)
        files_relevant = list(task_state.files_relevant)
        files_modified = list(task_state.files_modified)

        for observation in observations:
            tool = str(observation.get("tool") or observation.get("payload", {}).get("tool") or "unknown")
            ok = bool(observation.get("ok", observation.get("payload", {}).get("ok", True)))
            result = str(observation.get("result") or observation.get("output") or observation.get("error") or "")
            path = observation_path(observation)
            effects = self.tool_effects.get(tool, set())
            if path and path not in files_relevant:
                files_relevant.append(path)
            if path and ToolStateEffect.MARKS_MODIFIED_FILE in effects and path not in files_modified:
                files_modified.append(path)
            if ok and result:
                fact = extract_known_fact(tool, result, path, effects)
                if fact and fact not in known_facts:
                    known_facts.append(fact)
            if not ok:
                failure = f"{tool}: {truncate_inline(result, 180)}"
                if failure not in failed_attempts:
                    failed_attempts.append(failure)
            summary_parts.append(f"{tool} {'ok' if ok else 'failed'}: {truncate_inline(result, 220)}")

        summary = truncate_text(" | ".join(part for part in summary_parts if part), self.max_summary_chars)
        compressed_state = task_state.model_copy(
            update={
                "known_facts": known_facts[-20:],
                "failed_attempts": failed_attempts[-20:],
                "files_relevant": files_relevant[-20:],
                "files_modified": files_modified[-20:],
                "history_summary": summary,
            }
        )
        output_chars = len(compressed_state.model_dump_json())
        return CompressionResult(
            task_state=compressed_state,
            input_chars=input_chars,
            output_chars=output_chars,
            ratio=round(output_chars / input_chars, 4) if input_chars else 1.0,
            summary=summary,
            compressed_observations=len(observations),
            compressed_observation_ids=observation_ids(observations),
            compressed_turns=observation_turns(observations),
            evidence_refs=evidence_refs(observations),
        )

    def fallback_compress(
        self,
        task_state: TaskState,
        observations: list[dict[str, Any]],
        error: str,
    ) -> CompressionResult:
        input_text = "\n".join(observation_text(observation) for observation in observations)
        summary = truncate_text(f"Compression fallback after error: {error}. {input_text}", self.max_summary_chars)
        compressed_state = task_state.model_copy(update={"history_summary": summary})
        output_chars = len(compressed_state.model_dump_json())
        input_chars = len(input_text)
        return CompressionResult(
            task_state=compressed_state,
            input_chars=input_chars,
            output_chars=output_chars,
            ratio=round(output_chars / input_chars, 4) if input_chars else 1.0,
            summary=summary,
            fallback_used=True,
            compressed_observations=len(observations),
            compressed_observation_ids=observation_ids(observations),
            compressed_turns=observation_turns(observations),
            evidence_refs=evidence_refs(observations),
        )


def observation_text(observation: dict[str, Any]) -> str:
    return str(
        observation.get("result")
        or observation.get("output")
        or observation.get("error")
        or observation.get("payload")
        or observation
    )


def observation_path(observation: dict[str, Any]) -> str | None:
    metadata = observation.get("metadata") or observation.get("payload", {}).get("metadata") or {}
    path = metadata.get("path") or observation.get("path")
    return str(path) if path else None


def observation_ids(observations: list[dict[str, Any]]) -> list[str]:
    return [str(observation.get("id")) for observation in observations if observation.get("id")]


def observation_turns(observations: list[dict[str, Any]]) -> list[int]:
    turns: list[int] = []
    for observation in observations:
        turn = observation.get("turn")
        if isinstance(turn, int):
            turns.append(turn)
    return turns


def evidence_refs(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for observation in observations:
        ref: dict[str, Any] = {
            "id": observation.get("id"),
            "turn": observation.get("turn"),
            "tool": observation.get("tool") or observation.get("payload", {}).get("tool"),
            "ok": observation.get("ok", observation.get("payload", {}).get("ok")),
        }
        path = observation_path(observation)
        if path:
            ref["path"] = path
        refs.append({key: value for key, value in ref.items() if value is not None})
    return refs


def extract_known_fact(tool: str, result: str, path: str | None, effects: set[ToolStateEffect] | None = None) -> str | None:
    effects = effects or set()
    text = " ".join(result.split())
    lowered = text.lower()
    if ToolStateEffect.RECORDS_PATH_FACT in effects and path:
        return f"{tool} read relevant path: {path}"
    if "exit code" in lowered or "passed" in lowered or "failed" in lowered:
        return f"{tool}: {truncate_inline(text, 180)}"
    if ToolStateEffect.RECORDS_OUTPUT_FACT in effects:
        return f"{tool}: {truncate_inline(text, 180)}"
    return None


def truncate_inline(value: str, max_chars: int) -> str:
    return truncate_text(" ".join(value.split()), max_chars)


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + " [truncated]"
