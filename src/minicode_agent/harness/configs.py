from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, field_validator

from minicode_agent.config import normalize_memory_reflection_mode


class AblationConfig(BaseModel):
    """Feature switches used by Day 16 evaluation experiments."""

    name: str
    description: str
    enable_skills: bool = False
    enable_skill_rerank: bool = False
    enable_memory: bool = False
    enable_compression: bool = False
    enable_subagents: bool = False
    memory_reflection_mode: str = "off"

    @field_validator("memory_reflection_mode")
    @classmethod
    def validate_memory_reflection_mode(cls, value: str) -> str:
        return normalize_memory_reflection_mode(value)

    @property
    def uses_llm_memory(self) -> bool:
        return self.memory_reflection_mode == "llm"

    def agent_kwargs(self) -> dict[str, bool | str]:
        return {
            "enable_skills": self.enable_skills,
            "enable_skill_rerank": self.enable_skill_rerank,
            "enable_memory": self.enable_memory,
            "enable_compression": self.enable_compression,
            "enable_subagents": self.enable_subagents,
            "memory_reflection_mode": self.memory_reflection_mode,
        }


ABLATION_CONFIGS: dict[str, AblationConfig] = {
    "baseline": AblationConfig(
        name="baseline",
        description="No skill routing, memory, compression, or subagents.",
    ),
    "skill_only": AblationConfig(
        name="skill_only",
        description="Skill routing only.",
        enable_skills=True,
    ),
    "memory_skill": AblationConfig(
        name="memory_skill",
        description="Deterministic memory plus skill routing and LLM rerank.",
        enable_skills=True,
        enable_skill_rerank=True,
        enable_memory=True,
        memory_reflection_mode="deterministic",
    ),
    "memory_llm": AblationConfig(
        name="memory_llm",
        description="Skill routing, LLM rerank, and the LLM memory reflection experiment slot.",
        enable_skills=True,
        enable_skill_rerank=True,
        enable_memory=True,
        memory_reflection_mode="llm",
    ),
    "full": AblationConfig(
        name="full",
        description="Skills, LLM rerank, deterministic memory, compression, and reviewer subagents.",
        enable_skills=True,
        enable_skill_rerank=True,
        enable_memory=True,
        enable_compression=True,
        enable_subagents=True,
        memory_reflection_mode="deterministic",
    ),
    "full_llm_memory": AblationConfig(
        name="full_llm_memory",
        description="Full stack plus the LLM memory reflection experiment slot.",
        enable_skills=True,
        enable_skill_rerank=True,
        enable_memory=True,
        enable_compression=True,
        enable_subagents=True,
        memory_reflection_mode="llm",
    ),
}


def resolve_ablation_config(name: str) -> AblationConfig:
    normalized = name.strip() or "baseline"
    if normalized == "default":
        normalized = "full"
    try:
        return ABLATION_CONFIGS[normalized]
    except KeyError as exc:
        known = ", ".join(sorted(ABLATION_CONFIGS))
        raise ValueError(f"Unknown eval config: {name}. Known configs: {known}") from exc


def load_ablation_config_file(path: Path) -> AblationConfig:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8-sig"))
    config = AblationConfig.model_validate(payload)
    if not config.name.strip():
        raise ValueError("Config file must provide a non-empty name.")
    return config


def ablation_config_names() -> list[str]:
    return list(ABLATION_CONFIGS)
