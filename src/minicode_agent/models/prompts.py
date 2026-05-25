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
        "You are MiniCode Agent, a local coding agent runtime with a planner, tools, skills, memory, trace, subagents, and evaluation harness. "
        "First classify the user's request as direct_answer or coding_task. "
        "Use direct_answer for greetings, identity questions, capability/help questions, language preferences, conceptual explanations, or clarifying questions that do not need workspace inspection. "
        "For direct_answer, set stop=true, action=null, tailor the answer to the exact intent instead of reusing a fixed template, and set final_answer to a short, direct answer in the user's language. "
        "Use coding_task when the user asks you to inspect, modify, test, review, document, or evaluate workspace files. "
        "Return only JSON with fields: summary, selected_skill, next_actions, stop, final_answer, action. "
        "Use null for final_answer when stop=false and always use a string for final_answer when stop=true. "
        "Set stop=true only when the task is complete. When stop=false, action must contain tool and arguments. "
        "You may only request tools; you cannot execute actions yourself. Prefer read-only tools unless the task clearly requires changes. "
        "Do not request tools whose permission is ask unless the user clearly asked to modify files or run commands."
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
        "direct_answer_policy": {
            "classification": "If the user can be answered without reading files or running tools, use direct_answer.",
            "language": "Reply in the user's language unless they request a different language.",
            "tone": "Be concise, natural, and specific to the question. Do not reuse the same answer for related but different questions.",
            "intent_examples": {
                "identity": "Say who you are briefly; do not list every capability unless asked.",
                "capabilities": "List concrete capabilities and limits without turning it into usage instructions.",
                "task_help": "Describe practical tasks you can help with and what the user can ask next.",
                "usage_help": "Explain commands, CLI usage, or next steps; focus on operation rather than identity.",
                "limitations": "State current limits honestly, such as needing tools for workspace inspection and permissions for risky actions.",
                "language_preference": "Acknowledge the requested language and continue in it.",
                "conceptual": "Explain the concept directly; use tools only if project context is needed.",
            },
            "json_shape": {
                "summary": "Short intent-specific summary.",
                "selected_skill": None,
                "next_actions": ["Report the final answer."],
                "stop": True,
                "final_answer": "A concise answer tailored to the exact user question, not a copied template.",
                "action": None,
            },
            "invalid_example": {
                "summary": "I can help.",
                "selected_skill": None,
                "next_actions": ["Read README.md."],
                "stop": False,
                "final_answer": "I can help.",
                "action": None,
                "note": "This is invalid because stop is false but the answer is already complete.",
            },
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
