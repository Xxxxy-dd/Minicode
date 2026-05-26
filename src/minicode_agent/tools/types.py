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


class DuplicatePolicy(StrEnum):
    ALLOW = "allow"
    BLOCK_IDENTICAL_SUCCESS = "block_identical_success"


class ToolStateEffect(StrEnum):
    MARKS_MODIFIED_FILE = "marks_modified_file"
    RECORDS_PATH_FACT = "records_path_fact"
    RECORDS_OUTPUT_FACT = "records_output_fact"


class ToolIntent(StrEnum):
    FILE_READ = "file_read"
    FILE_SEARCH = "file_search"
    FILE_OVERWRITE = "file_overwrite"
    FILE_APPEND = "file_append"
    FILE_CREATE = "file_create"
    FILE_DELETE = "file_delete"
    FILE_EDIT = "file_edit"
    COMMAND_RUN = "command_run"
    TEST_RUN = "test_run"
    REPO_INSPECT = "repo_inspect"


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    permission: PermissionMode
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.ALLOW
    state_effects: tuple[ToolStateEffect, ...] = ()
    intents: tuple[ToolIntent, ...] = ()
    capture_full_output: bool = False
    counts_as_subagent_call: bool = False
    subagent_roles: tuple[str, ...] = ()
    path_arg_names: tuple[str, ...] = ()
    command_arg_names: tuple[str, ...] = ()
    capability: str | None = None
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
