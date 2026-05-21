import json

from minicode_agent.models.client import ModelMessage
from minicode_agent.tools.registry import ToolRegistry


def build_planning_prompt(goal: str, known_files: list[str], registry: ToolRegistry) -> list[ModelMessage]:
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
        "You are MiniCode Agent's planner. Choose exactly one safe next tool action. "
        "Return only JSON with fields: summary, selected_skill, next_actions, action. "
        "The action object must contain tool and arguments. Prefer read-only tools unless the task clearly requires changes."
    )
    user_payload = {
        "goal": goal,
        "known_files": known_files[:50],
        "available_tools": tools,
        "response_example": {
            "summary": "Inspect project documentation first.",
            "selected_skill": None,
            "next_actions": ["Read README.md.", "Use the result to plan the next step."],
            "action": {"tool": "read_file", "arguments": {"path": "README.md"}},
        },
    }
    return [
        ModelMessage(role="system", content=system),
        ModelMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False, indent=2)),
    ]
