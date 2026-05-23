from pathlib import Path
import os

from pydantic import BaseModel, ConfigDict, Field, field_validator


MEMORY_REFLECTION_MODES = frozenset({"off", "deterministic", "llm"})


def normalize_memory_reflection_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in MEMORY_REFLECTION_MODES:
        known = ", ".join(sorted(MEMORY_REFLECTION_MODES))
        raise ValueError(f"memory_reflection_mode must be one of: {known}")
    return normalized


class MiniCodeConfig(BaseModel):
    """Runtime configuration for a MiniCode Agent session."""

    model_config = ConfigDict(frozen=True)

    workspace: Path = Field(default_factory=Path.cwd)
    trace_db_path: Path | None = None
    max_agent_steps: int = 30
    max_subagent_steps: int = 8
    max_failed_tool_attempts: int = 2
    require_approval_for_writes: bool = True
    model_name: str | None = None
    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: str | None = None

    @classmethod
    def from_env(cls, workspace: Path) -> "MiniCodeConfig":
        return cls(
            workspace=workspace,
            model_name=os.getenv("MINICODE_MODEL") or os.getenv("OPENAI_MODEL"),
            model_base_url=os.getenv("MINICODE_MODEL_BASE_URL", "https://api.openai.com/v1"),
            model_api_key=os.getenv("MINICODE_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY"),
        )

    @field_validator("workspace")
    @classmethod
    def normalize_workspace(cls, value: Path) -> Path:
        return value.expanduser().resolve()
