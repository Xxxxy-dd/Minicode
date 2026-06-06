from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SubagentRole(StrEnum):
    EXPLORER = "explorer"
    REVIEWER = "reviewer"
    SECURITY_REVIEWER = "security-reviewer"
    TESTER = "tester"
    IMPLEMENTER = "implementer"


class SubagentRequest(BaseModel):
    role: SubagentRole
    task: str
    max_steps: int = 3
    path: str | None = None
    pattern: str | None = None

    @field_validator("max_steps")
    @classmethod
    def validate_max_steps(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_steps must be at least 1")
        return min(value, 5)


class SubagentResult(BaseModel):
    role: SubagentRole
    task: str
    ok: bool
    summary: str
    findings: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    security_findings: list[dict] = Field(default_factory=list)
    test_suggestions: list[str] = Field(default_factory=list)
    test_results: list[dict] = Field(default_factory=list)
    merge_blockers: list[str] = Field(default_factory=list)
    patch_proposal: dict | None = None
    tool_calls: int = 0
    stopped_reason: str
