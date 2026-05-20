from enum import StrEnum

from pydantic import BaseModel, Field


class AgentPhase(StrEnum):
    INIT = "init"
    LOAD_CONTEXT = "load_context"
    SELECT_SKILL = "select_skill"
    PLAN = "plan"
    ACT = "act"
    OBSERVE = "observe"
    COMPRESS_CONTEXT = "compress_context"
    VERIFY = "verify"
    REFLECT = "reflect"
    DONE = "done"
    FAILED = "failed"
    NEED_APPROVAL = "need_approval"


class TaskState(BaseModel):
    goal: str
    constraints: list[str] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    failed_attempts: list[str] = Field(default_factory=list)
    files_relevant: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class RunMetrics(BaseModel):
    tool_calls: int = 0
    retries: int = 0
    permission_blocks: int = 0
    subagent_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class AgentState(BaseModel):
    run_id: str
    workspace: str
    user_goal: str
    current_phase: AgentPhase = AgentPhase.INIT
    selected_skills: list[str] = Field(default_factory=list)
    task_state: TaskState
    tool_history: list[str] = Field(default_factory=list)
    files_touched: list[str] = Field(default_factory=list)
    approval_events: list[str] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
