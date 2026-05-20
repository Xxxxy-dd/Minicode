from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MiniCodeConfig(BaseModel):
    """Runtime configuration for a MiniCode Agent session."""

    model_config = ConfigDict(frozen=True)

    workspace: Path = Field(default_factory=Path.cwd)
    trace_db_path: Path | None = None
    max_agent_steps: int = 30
    max_subagent_steps: int = 8
    require_approval_for_writes: bool = True

    @field_validator("workspace")
    @classmethod
    def normalize_workspace(cls, value: Path) -> Path:
        return value.expanduser().resolve()
