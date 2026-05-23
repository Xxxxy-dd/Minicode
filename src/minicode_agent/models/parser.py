import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelAction:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelPlan:
    summary: str
    next_actions: list[str]
    action: ModelAction | None
    selected_skill: str | None = None
    stop: bool = False
    final_answer: str | None = None


def parse_model_plan(content: str) -> ModelPlan:
    """Parse the structured planner response expected from the model."""
    normalized = content.strip()
    json_text = extract_json_object(normalized)
    try:
        payload = json.loads(json_text or normalized)
    except json.JSONDecodeError as exc:
        if normalized:
            return ModelPlan(
                summary="Model returned a direct answer.",
                next_actions=["Report the direct model answer."],
                action=None,
                stop=True,
                final_answer=normalized,
            )
        raise ValueError("Model response must be valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")

    summary = _required_str(payload, "summary")
    next_actions = _string_list(payload.get("next_actions"), "next_actions")
    stop = payload.get("stop", False)
    if not isinstance(stop, bool):
        raise ValueError("Model response field 'stop' must be a boolean.")

    final_answer = payload.get("final_answer")
    if final_answer is not None and not isinstance(final_answer, str):
        raise ValueError("Model response field 'final_answer' must be a string or null.")
    if stop and (not isinstance(final_answer, str) or not final_answer.strip()):
        raise ValueError("Model response field 'final_answer' must be a non-empty string when stop is true.")

    action = _parse_action(payload.get("action"), stop)

    selected_skill = payload.get("selected_skill")
    if selected_skill is not None and not isinstance(selected_skill, str):
        raise ValueError("Model response field 'selected_skill' must be a string or null.")

    return ModelPlan(
        summary=summary,
        next_actions=next_actions,
        action=action,
        selected_skill=selected_skill,
        stop=stop,
        final_answer=final_answer,
    )


def extract_json_object(content: str) -> str | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1].strip()
    return None


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Model response field '{key}' must be a non-empty string.")
    return value


def _string_list(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Model response field '{key}' must be a non-empty list.")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"Model response field '{key}' must contain only non-empty strings.")
    return value


def _parse_action(value: Any, stop: bool) -> ModelAction | None:
    if value is None:
        if stop:
            return None
        raise ValueError("Model response field 'action' must be an object when stop is false.")
    if not isinstance(value, dict):
        raise ValueError("Model response field 'action' must be an object or null.")

    tool = _required_str(value, "tool")
    arguments = value.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("Model response field 'action.arguments' must be an object.")
    return ModelAction(tool=tool, arguments=arguments)
