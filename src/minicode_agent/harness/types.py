from pathlib import Path
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class ExpectedOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ANALYSIS_ONLY = "analysis_only"


class SuccessCommand(BaseModel):
    command: str
    exit_code: int = 0
    timeout_seconds: int = 30


class SetupCommand(BaseModel):
    command: str
    exit_code: int = 0
    timeout_seconds: int = 30


class SetupToolCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    approved: bool = False


class TraceAssertion(BaseModel):
    event_type: str
    min_count: int = 1
    payload_contains: dict[str, str] = Field(default_factory=dict)


class ForbiddenToolAssertion(BaseModel):
    tool: str


class FileDiffAssertion(BaseModel):
    path: str
    should_change: bool = True


class TeamAssertion(BaseModel):
    role: str
    require_evidence: bool = True
    require_merge_blocker: bool = False


class HarnessTask(BaseModel):
    id: str
    workspace: Path = Path(".")
    prompt: str
    setup: list[SetupCommand] = Field(default_factory=list)
    setup_tools: list[SetupToolCall] = Field(default_factory=list)
    success: list[SuccessCommand] = Field(default_factory=list)
    trace_assertions: list[TraceAssertion] = Field(default_factory=list)
    forbidden_tools: list[ForbiddenToolAssertion] = Field(default_factory=list)
    file_diff_assertions: list[FileDiffAssertion] = Field(default_factory=list)
    team_assertions: list[TeamAssertion] = Field(default_factory=list)
    expected: ExpectedOutcome = ExpectedOutcome.PASS
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    difficulty: str = "easy"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("task id cannot be empty")
        return normalized


class SuccessResult(BaseModel):
    command: str
    expected_exit_code: int
    exit_code: int | None
    passed: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""


class SetupToolResult(BaseModel):
    tool: str
    ok: bool
    error: str | None = None
    output_summary: str = ""


class AssertionResult(BaseModel):
    kind: str
    target: str
    passed: bool
    detail: str = ""


class EvalResult(BaseModel):
    task_id: str
    config: str
    expected: ExpectedOutcome
    category: str
    tags: list[str] = Field(default_factory=list)
    difficulty: str
    prompt: str
    source_workspace: str
    workspace: str
    run_id: str
    passed: bool
    agent_ok: bool
    runtime_seconds: float
    setup_results: list[SuccessResult] = Field(default_factory=list)
    setup_tool_results: list[SetupToolResult] = Field(default_factory=list)
    success_results: list[SuccessResult] = Field(default_factory=list)
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    config_features: dict = Field(default_factory=dict)
    memory_summary: str | None = None
    trace_path: str
