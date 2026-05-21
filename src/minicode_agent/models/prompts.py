import json

from typing import Any

from minicode_agent.models.client import ModelMessage
from minicode_agent.tools.registry import ToolRegistry


def build_planning_prompt(
    goal: str,
    known_files: list[str],
    registry: ToolRegistry,
    observations: list[dict[str, Any]] | None = None,
) -> list[ModelMessage]:
    tools = [
        {
            "name": tool.spec.name,
            "description": tool.spec.description,
            "risk_level": tool.spec.risk_level.value,
            "permission": tool.spec.permission.value,
            "input_schema": tool.spec.input_schema,
        }
        for tool in registry.list()
    ]
    system = (
        "You are MiniCode Agent's planner. Decide the next step in a bounded tool loop. "
        "Return only JSON with fields: summary, selected_skill, next_actions, stop, final_answer, action. "
        "Set stop=true only when the task is complete. When stop=false, action must contain tool and arguments. "
        "You may only request tools; you cannot execute actions yourself. Prefer read-only tools unless the task clearly requires changes."
    )
    user_payload = {
        "goal": goal,
        "known_files": known_files[:50],
        "recent_observations": (observations or [])[-10:],
        "available_tools": tools,
        "response_example": {
            "summary": "Inspect project documentation first.",
            "selected_skill": None,
            "next_actions": ["Read README.md.", "Use the result to plan the next step."],
            "stop": False,
            "final_answer": None,
            "action": {"tool": "read_file", "arguments": {"path": "README.md"}},
        },
        "stop_example": {
            "summary": "The project was inspected.",
            "selected_skill": None,
            "next_actions": ["Report the result."],
            "stop": True,
            "final_answer": "README.md was inspected and no further tool calls are needed.",
            "action": None,
        },
    }
    return [
        ModelMessage(role="system", content=system),
        ModelMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False, indent=2)),
    ]
