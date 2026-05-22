import json

from typing import Any

from minicode_agent.core.state import TaskState
from minicode_agent.memory import MemoryRecord
from minicode_agent.models.client import ModelMessage
from minicode_agent.skills import SkillDefinition
from minicode_agent.tools.registry import ToolRegistry

MAX_SKILL_CONTENT_CHARS = 1200
MAX_MEMORY_CONTENT_CHARS = 500
MAX_MEMORY_PROMPT_CHARS = 2000
MAX_MEMORY_PROMPT_RECORDS = 8


def build_planning_prompt(
    goal: str,
    known_files: list[str],
    registry: ToolRegistry,
    observations: list[dict[str, Any]] | None = None,
    skills: list[SkillDefinition] | None = None,
    memories: list[MemoryRecord] | None = None,
    task_state: TaskState | None = None,
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
        "task_state": task_state.model_dump() if task_state else {"goal": goal},
        "known_files": known_files[:50],
        "recent_observations": (observations or [])[-10:],
        "active_skills": [skill_prompt_payload(skill) for skill in (skills or [])],
        "relevant_memory": memory_prompt_payloads(memories or []),
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


def skill_prompt_payload(skill: SkillDefinition) -> dict[str, Any]:
    return {
        "name": skill.name,
        "description": skill.metadata.description,
        "tags": skill.metadata.tags,
        "applies_to": skill.metadata.applies_to,
        "aliases": skill.metadata.aliases,
        "content": truncate_skill_content(skill.content),
    }


def truncate_skill_content(content: str) -> str:
    if len(content) <= MAX_SKILL_CONTENT_CHARS:
        return content
    return content[:MAX_SKILL_CONTENT_CHARS] + "\n[truncated]"


def memory_prompt_payload(memory: MemoryRecord) -> dict[str, Any]:
    return {
        "kind": memory.kind.value,
        "content": truncate_memory_content(memory.content),
        "confidence": memory.confidence,
        "source_run_id": memory.source_run_id,
        "tags": memory.tags,
        "reason": memory.reason,
    }


def memory_prompt_payloads(memories: list[MemoryRecord]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    used_chars = 0
    for memory in memories[:MAX_MEMORY_PROMPT_RECORDS]:
        payload = memory_prompt_payload(memory)
        content_chars = len(str(payload["content"]))
        if used_chars + content_chars > MAX_MEMORY_PROMPT_CHARS:
            break
        used_chars += content_chars
        payloads.append(payload)
    return payloads


def truncate_memory_content(content: str) -> str:
    if len(content) <= MAX_MEMORY_CONTENT_CHARS:
        return content
    return content[:MAX_MEMORY_CONTENT_CHARS] + "\n[truncated]"
