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


class HarnessTask(BaseModel):
    id: str
    workspace: Path = Path(".")
    prompt: str
    success: list[SuccessCommand] = Field(default_factory=list)
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
    success_results: list[SuccessResult] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    config_features: dict = Field(default_factory=dict)
    memory_summary: str | None = None
    trace_path: str
