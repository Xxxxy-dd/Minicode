import json
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
    action: ModelAction
    selected_skill: str | None = None


def parse_model_plan(content: str) -> ModelPlan:
    """Parse the structured planner response expected from the model."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Model response must be valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")

    summary = _required_str(payload, "summary")
    next_actions = _string_list(payload.get("next_actions"), "next_actions")
    action_payload = payload.get("action")
    if not isinstance(action_payload, dict):
        raise ValueError("Model response field 'action' must be an object.")

    tool = _required_str(action_payload, "tool")
    arguments = action_payload.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("Model response field 'action.arguments' must be an object.")

    selected_skill = payload.get("selected_skill")
    if selected_skill is not None and not isinstance(selected_skill, str):
        raise ValueError("Model response field 'selected_skill' must be a string or null.")

    return ModelPlan(
        summary=summary,
        next_actions=next_actions,
        action=ModelAction(tool=tool, arguments=arguments),
        selected_skill=selected_skill,
    )


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
