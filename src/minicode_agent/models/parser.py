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

    stop = payload.get("stop", False)
    if not isinstance(stop, bool):
        raise ValueError("Model response field 'stop' must be a boolean.")

    final_answer = payload.get("final_answer")
    if final_answer is not None and not isinstance(final_answer, str):
        raise ValueError("Model response field 'final_answer' must be a string or null.")
    summary = optional_text(payload.get("summary")) or optional_text(final_answer) or "Model completed the turn."
    if not stop and payload.get("action") is None and direct_answer_payload(payload):
        stop = True
        final_answer = optional_text(final_answer) or summary
    next_actions = optional_string_list(payload.get("next_actions")) or default_next_actions(stop)
    if stop and (not isinstance(final_answer, str) or not final_answer.strip()):
        final_answer = summary

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


def optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def optional_string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return cleaned or None


def default_next_actions(stop: bool) -> list[str]:
    return ["Report the final answer."] if stop else ["Choose the next safe tool action."]


def direct_answer_payload(payload: dict[str, Any]) -> bool:
    has_answer_text = bool(optional_text(payload.get("summary")) or optional_text(payload.get("final_answer")))
    actions = optional_string_list(payload.get("next_actions")) or []
    has_tool_intent = any(is_tool_intent_action(action) for action in actions)
    return has_answer_text and not has_tool_intent


def is_tool_intent_action(value: str) -> bool:
    normalized = value.strip().lower()
    tool_phrases = (
        "read file",
        "read the file",
        "read readme",
        "write file",
        "edit file",
        "modify file",
        "update file",
        "run command",
        "run shell",
        "run test",
        "run tests",
        "search code",
        "search file",
        "search for",
        "grep for",
        "find in code",
        "inspect file",
        "inspect project",
        "inspect workspace",
        "list files",
        "list project files",
        "call tool",
        "use tool",
        "execute command",
        "open file",
        "读取文件",
        "阅读文件",
        "读取 readme",
        "阅读 readme",
        "写入文件",
        "编辑文件",
        "修改文件",
        "运行命令",
        "执行命令",
        "运行测试",
        "搜索代码",
        "搜索文件",
        "搜索项目",
        "查找代码",
        "查找文件",
        "查看文件",
        "检查项目",
        "检查工作区",
        "列出文件",
        "列出项目文件",
        "调用工具",
        "使用工具",
    )
    if any(phrase in normalized for phrase in tool_phrases):
        return True
    return bool(re.search(r"\b(read|write|edit|modify|update|inspect|open)\s+[\w./\\-]+\.\w+\b", normalized))


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
