from dataclasses import dataclass
from time import perf_counter
from typing import Any

from minicode_agent.memory import MemoryRecord
from minicode_agent.core.state import TaskState
from minicode_agent.models import ModelClient, ModelResponse, build_planning_prompt, parse_model_plan
from minicode_agent.skills import SkillDefinition, SkillRouter
from minicode_agent.trace.store import TraceStore
from minicode_agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class PlannedAction:
    tool: str
    arguments: dict[str, Any]
    description: str


@dataclass(frozen=True)
class ModelDecision:
    summary: str
    next_actions: list[str]
    action: PlannedAction | None
    selected_skill: str | None = None
    stop: bool = False
    final_answer: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class RuleBasedPlanner:
    """Small deterministic planner used before model-driven planning exists."""

    def __init__(self, skill_router: SkillRouter | None = None) -> None:
        self.skill_router = skill_router or SkillRouter()

    def select_skill(self, goal: str) -> str | None:
        result = self.skill_router.route(goal)
        return result.selected[0] if result.selected else None

    def plan_steps(self, goal: str) -> list[str]:
        return [
            "Inspect relevant files.",
            "Choose the smallest safe action.",
            "Verify the result with traceable evidence.",
        ]

    def next_action(self, goal: str, known_files: list[str]) -> PlannedAction:
        normalized = goal.lower()
        wants_review = any(token in normalized for token in ("review", "audit", "code review", "审查"))
        references_change = any(token in normalized for token in ("diff", "change", "current", "修改", "变更"))
        if wants_review and references_change:
            return PlannedAction(
                tool="spawn_subagent",
                arguments={"role": "reviewer", "task": goal, "max_steps": 2},
                description="Use the bounded reviewer subagent to inspect the current diff.",
            )
        if "README.md" in known_files:
            return PlannedAction(
                tool="read_file",
                arguments={"path": "README.md"},
                description="Inspect README.md as a safe first context read.",
            )
        return PlannedAction(
            tool="list_files",
            arguments={},
            description="List workspace files because README.md is not present.",
        )


class ModelDrivenPlanner:
    """Planner backed by a structured model response."""

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        trace_store: TraceStore | None = None,
        run_id: str | None = None,
    ) -> None:
        self.model_client = model_client
        self.registry = registry
        self.trace_store = trace_store
        self.run_id = run_id

    def plan(
        self,
        goal: str,
        known_files: list[str],
        observations: list[dict[str, Any]] | None = None,
        skills: list[SkillDefinition] | None = None,
        memories: list[MemoryRecord] | None = None,
        task_state: TaskState | None = None,
        turn_index: int | None = None,
        failed_tool_attempts: int = 0,
    ) -> ModelDecision:
        messages = build_planning_prompt(
            goal,
            known_files,
            self.registry,
            observations=observations,
            skills=skills,
            memories=memories,
            task_state=task_state,
        )
        started_at = perf_counter()
        self._trace(
            "model_requested",
            {
                "messages": len(messages),
                "known_files": len(known_files),
                "observation_count": len(observations or []),
                "skill_count": len(skills or []),
                "memory_count": len(memories or []),
                "turn": turn_index,
                "failed_tool_attempts": failed_tool_attempts,
            },
        )
        try:
            response = self.model_client.complete(messages)
            plan = parse_model_plan(response.content)
            if plan.action:
                self.registry.get(plan.action.tool)
        except Exception as exc:
            self._trace(
                "model_failed",
                {
                    "error": str(exc),
                    "turn": turn_index,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
            raise

        self._trace(
            "model_finished",
            {
                "ok": True,
                "tool": plan.action.tool if plan.action else None,
                "stop": plan.stop,
                "turn": turn_index,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "metadata": response.metadata,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        action = None
        if plan.action:
            action = PlannedAction(
                tool=plan.action.tool,
                arguments=plan.action.arguments,
                description=plan.summary,
            )
        return ModelDecision(
            summary=plan.summary,
            next_actions=plan.next_actions,
            action=action,
            selected_skill=plan.selected_skill,
            stop=plan.stop,
            final_answer=plan.final_answer,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.trace_store is None or self.run_id is None:
            return
        self.trace_store.append(self.run_id, event_type, payload)
