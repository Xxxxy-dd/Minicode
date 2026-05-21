from dataclasses import dataclass
from typing import Any

from minicode_agent.core.state import AgentPhase, AgentState, RunMetrics, TaskState
from minicode_agent.agent.planner import ModelDrivenPlanner, PlannedAction, RuleBasedPlanner
from minicode_agent.models import ModelClient
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext


@dataclass
class AgentRunResult:
    state: AgentState
    transcript: list[dict[str, Any]]


class AgentLoop:
    """Minimal rule-driven agent loop for day 6."""

    def __init__(
        self,
        runtime: RuntimeContext,
        goal: str,
        max_steps: int = 30,
        model_client: ModelClient | None = None,
    ) -> None:
        self.runtime = runtime
        self.max_steps = max_steps
        self.steps = 0
        self.state = AgentState(
            run_id=runtime.run_id,
            workspace=str(runtime.workspace),
            user_goal=goal,
            current_phase=AgentPhase.INIT,
            task_state=TaskState(goal=goal),
            metrics=RunMetrics(),
        )
        self.transcript: list[dict[str, Any]] = []
        registry = create_default_registry()
        self.executor = ToolExecutor(
            registry,
            trace_store=runtime.trace_store,
            run_id=runtime.run_id,
        )
        self.rule_planner = RuleBasedPlanner()
        self.model_planner = (
            ModelDrivenPlanner(model_client, registry, trace_store=runtime.trace_store, run_id=runtime.run_id)
            if model_client
            else None
        )
        self.failure_reason: str | None = None

    def run(self) -> AgentRunResult:
        self.runtime.trace_store.append(
            self.runtime.run_id,
            "run_started",
            {
                "goal": self.state.user_goal,
                "workspace": self.state.workspace,
                "max_steps": self.max_steps,
            },
        )
        self._phase(AgentPhase.INIT, "Initialize agent state.")

        self._phase(AgentPhase.LOAD_CONTEXT, "Load workspace rules and trace context.")
        known_files = self._load_context()
        self._observe("context_loaded", {"workspace": self.state.workspace, "known_files": known_files[:20]})

        self._phase(AgentPhase.SELECT_SKILL, "Select a skill for the task.")
        selected_skill = self.rule_planner.select_skill(self.state.user_goal)
        if selected_skill:
            self.state.selected_skills = [selected_skill]
        self._observe("skill_selected", {"skills": self.state.selected_skills})

        self._phase(AgentPhase.PLAN, "Draft a short plan.")
        planned_action = self._plan_action(known_files)
        if planned_action is None:
            return self._finish(False)
        self._observe(
            "agent_planned",
            {
                "next_actions": self.state.task_state.next_actions,
                "tool": planned_action.tool,
                "description": planned_action.description,
                "planner": "model" if self.model_planner else "rules",
            },
        )

        self._phase(AgentPhase.ACT, "Take a small safe action.")
        action_result = self._act(planned_action.tool, planned_action.arguments)
        self._observe("action_result", action_result)

        self._phase(AgentPhase.OBSERVE, "Inspect the result.")
        self._observe("agent_observed", {"result": action_result["result"], "ok": action_result["ok"]})

        self._phase(AgentPhase.VERIFY, "Verify the run outcome.")
        verified = self._verify(action_result)
        self._observe("verification", {"verified": verified})

        self._phase(AgentPhase.REFLECT, "Capture a short reflection.")
        self._reflect()
        self._observe("reflection", {"files_touched": self.state.files_touched})

        return self._finish(verified)

    def _finish(self, verified: bool) -> AgentRunResult:
        final_phase = AgentPhase.DONE if verified else AgentPhase.FAILED
        self._phase(final_phase, "Finish the run.")
        self.runtime.trace_store.append(
            self.runtime.run_id,
            "run_finished",
            {
                "ok": verified,
                "final_phase": self.state.current_phase.value,
                "reason": self.failure_reason,
                "metrics": self.state.metrics.model_dump(),
                "selected_skills": self.state.selected_skills,
            },
        )
        return AgentRunResult(state=self.state, transcript=self.transcript)

    def _load_context(self) -> list[str]:
        observation = self._execute_tool("list_files", {})
        if not observation.ok:
            self.failure_reason = observation.error
            return []
        return [line.rstrip("/") for line in observation.output.splitlines() if line]

    def _act(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        context = ToolContext(workspace=self.runtime.workspace)
        observation = self._execute_tool(tool, arguments)
        if observation.ok:
            path = arguments.get("path")
            if path:
                self.state.files_touched.append(str(path))
                self.state.task_state.files_relevant = [str(path)]
            return {
                "tool": tool,
                "ok": True,
                "result": f"executed {tool}",
            }

        self.failure_reason = observation.error
        return {
            "tool": tool,
            "ok": False,
            "result": observation.error,
        }

    def _verify(self, action_result: dict[str, Any]) -> bool:
        self.state.metrics.retries += 0
        ok = bool(action_result.get("ok"))
        if not ok:
            self.state.task_state.failed_attempts.append(str(action_result.get("result")))
        return ok

    def _reflect(self) -> None:
        self.state.task_state.decisions.append("Keep the first loop minimal and traceable.")

    def _plan_action(self, known_files: list[str]) -> PlannedAction | None:
        if not self.model_planner:
            self.state.task_state.next_actions = self.rule_planner.plan_steps(self.state.user_goal)
            return self.rule_planner.next_action(self.state.user_goal, known_files)

        try:
            planned_action = self.model_planner.plan(self.state.user_goal, known_files)
        except Exception as exc:
            self.failure_reason = str(exc)
            self.state.task_state.failed_attempts.append(str(exc))
            self._observe("planning_failed", {"reason": str(exc), "planner": "model"})
            return None
        if self.model_planner.selected_skill:
            self.state.selected_skills = [self.model_planner.selected_skill]
        self.state.task_state.next_actions = self.model_planner.next_actions
        if self.model_planner.last_response:
            self.state.metrics.input_tokens += self.model_planner.last_response.input_tokens
            self.state.metrics.output_tokens += self.model_planner.last_response.output_tokens
        return planned_action

    def _phase(self, phase: AgentPhase, reason: str) -> None:
        self.state.current_phase = phase
        self.runtime.trace_store.append(
            self.runtime.run_id,
            "phase_changed",
            {
                "phase": phase.value,
                "reason": reason,
            },
        )

    def _observe(self, event_type: str, payload: dict[str, Any]) -> None:
        self.transcript.append({"event": event_type, "payload": payload})
        self.runtime.trace_store.append(self.runtime.run_id, event_type, payload)

    def _execute_tool(self, name: str, arguments: dict[str, Any]):
        if self.steps >= self.max_steps:
            self.failure_reason = "max agent steps exceeded"
            raise RuntimeError(self.failure_reason)
        self.steps += 1
        observation = self.executor.execute(name, ToolContext(workspace=self.runtime.workspace), arguments)
        self.state.metrics.tool_calls += 1
        if not observation.ok and observation.metadata.get("permission") == "deny":
            self.state.metrics.permission_blocks += 1
        return observation
