from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from minicode_agent.skills import SkillDefinition, default_skill_registry
from minicode_agent.tools.registry import ToolRegistry, create_default_registry


def build_capability_profile(workspace: Path | None = None, tool_registry: ToolRegistry | None = None) -> dict[str, Any]:
    registry = tool_registry or create_default_registry()
    skills = default_skill_registry(workspace).list()
    return {
        "tools": [
            {
                "name": tool.spec.name,
                "description": tool.spec.description,
                "risk_level": tool.spec.risk_level.value,
                "permission": tool.spec.permission.value,
                "capability": tool.spec.capability,
                "intents": [intent.value for intent in tool.spec.intents],
            }
            for tool in registry.list()
        ],
        "skills": [skill_capability_payload(skill) for skill in skills],
        "commands": [
            "/help",
            "/status",
            "/memory",
            "/skills",
            "/trace",
            "/diff",
            "/tools",
            "/config",
            "/last",
            "/clear",
            "/exit",
        ],
    }


def skill_capability_payload(skill: SkillDefinition) -> dict[str, Any]:
    return {
        "name": skill.name,
        "description": skill.metadata.description,
        "tags": skill.metadata.tags,
        "applies_to": skill.metadata.applies_to,
        "aliases": skill.metadata.aliases,
    }


def capability_reply(profile: dict[str, Any], *, subject: str = "overview", chinese: bool = True) -> str:
    payload = {
        "fallback": "no_model",
        "subject": subject,
        "capability_profile": filter_capability_profile(profile, subject),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def filter_capability_profile(profile: dict[str, Any], subject: str) -> dict[str, Any]:
    if subject == "skills":
        return {"skills": profile.get("skills", [])}
    if subject == "commands":
        return {"commands": profile.get("commands", [])}
    if subject == "tools":
        return {"tools": profile.get("tools", [])}
    return profile
