from dataclasses import dataclass
from time import perf_counter
from typing import Any

from minicode_agent.models import ModelClient, ModelResponse, build_planning_prompt, parse_model_plan
from minicode_agent.trace.store import TraceStore
from minicode_agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class PlannedAction:
    tool: str
    arguments: dict[str, Any]
    description: str


class RuleBasedPlanner:
    """Small deterministic planner used before model-driven planning exists."""

    def select_skill(self, goal: str) -> str | None:
        normalized = goal.lower()
        if any(token in normalized for token in ("write test", "add test", "test case", "unit test")):
            return "test-writing"
        if any(token in normalized for token in ("review", "audit", "check diff", "code review")):
            return "code-review"
        if any(token in normalized for token in ("bug", "fail", "failing", "error", "pytest")):
            return "debugging"
        return None

    def plan_steps(self, goal: str) -> list[str]:
        return [
            "Inspect relevant files.",
            "Choose the smallest safe action.",
            "Verify the result with traceable evidence.",
        ]

    def next_action(self, goal: str, known_files: list[str]) -> PlannedAction:
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
        self.selected_skill: str | None = None
        self.next_actions: list[str] = []
        self.summary: str = ""
        self.last_response: ModelResponse | None = None

    def plan(self, goal: str, known_files: list[str]) -> PlannedAction:
        messages = build_planning_prompt(goal, known_files, self.registry)
        started_at = perf_counter()
        self._trace(
            "model_requested",
            {
                "messages": len(messages),
                "known_files": len(known_files),
            },
        )
        try:
            response = self.model_client.complete(messages)
            self.last_response = response
            plan = parse_model_plan(response.content)
            self.registry.get(plan.action.tool)
        except Exception as exc:
            self._trace(
                "model_failed",
                {
                    "error": str(exc),
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
            raise

        self.selected_skill = plan.selected_skill
        self.next_actions = plan.next_actions
        self.summary = plan.summary
        self._trace(
            "model_finished",
            {
                "ok": True,
                "tool": plan.action.tool,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "metadata": response.metadata,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        return PlannedAction(
            tool=plan.action.tool,
            arguments=plan.action.arguments,
            description=plan.summary,
        )

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.trace_store is None or self.run_id is None:
            return
        self.trace_store.append(self.run_id, event_type, payload)
