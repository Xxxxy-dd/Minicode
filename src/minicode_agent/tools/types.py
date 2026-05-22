from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from minicode_agent.trace import TraceStore


class RiskLevel(StrEnum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


class PermissionMode(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    permission: PermissionMode
    timeout_seconds: int = 30


class ToolObservation(BaseModel):
    tool_call_id: str
    ok: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False


class ToolContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace: Path
    trace_store: TraceStore | None = None
    run_id: str | None = None

    @property
    def resolved_workspace(self) -> Path:
        return self.workspace.expanduser().resolve()
