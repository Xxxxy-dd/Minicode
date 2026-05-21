from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from minicode_agent.trace import TraceStore, default_trace_db_path


class RuntimeContext(BaseModel):
    """Shared runtime context for CLI commands, agent runs, and harness jobs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace: Path
    run_id: str = Field(default_factory=lambda: f"tool_{uuid4().hex[:8]}")
    trace_store: TraceStore

    @classmethod
    def create(cls, workspace: Path, run_id: str | None = None) -> "RuntimeContext":
        resolved_workspace = workspace.expanduser().resolve()
        return cls(
            workspace=resolved_workspace,
            run_id=run_id or f"tool_{uuid4().hex[:8]}",
            trace_store=TraceStore(default_trace_db_path(resolved_workspace)),
        )

    @field_validator("workspace")
    @classmethod
    def normalize_workspace(cls, value: Path) -> Path:
        return value.expanduser().resolve()
