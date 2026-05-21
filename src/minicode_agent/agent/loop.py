from dataclasses import dataclass
from typing import Any

from minicode_agent.core.state import AgentPhase, AgentState, RunMetrics, TaskState
from minicode_agent.agent.planner import ModelDecision, ModelDrivenPlanner, PlannedAction, RuleBasedPlanner
from minicode_agent.models import ModelClient
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext, ToolObservation


MAX_OBSERVATION_CHARS = 4000


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
        max_failed_tool_attempts: int = 2,
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
        self.observations: list[dict[str, Any]] = []
        self.max_failed_tool_attempts = max_failed_tool_attempts
        self.loop_turns = 0

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
        if self.model_planner:
            return self._run_model_loop(known_files)

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

    def _run_model_loop(self, known_files: list[str]) -> AgentRunResult:
        failed_tool_attempts = 0
        while self.loop_turns < self.max_steps:
            turn_index = self.loop_turns + 1
            self.loop_turns = turn_index
            decision = self._plan_model_decision(known_files, turn_index, failed_tool_attempts)
            if decision is None:
                return self._finish(False)

            self._observe(
                "agent_planned",
                {
                    "next_actions": self.state.task_state.next_actions,
                    "tool": decision.action.tool if decision.action else None,
                    "description": decision.summary,
                    "planner": "model",
                    "stop": decision.stop,
                    "turn": turn_index,
                },
            )
            if decision.stop:
                verified = self._verify_model_stop(decision)
                self._phase(AgentPhase.VERIFY, "Verify model stop decision.")
                self._observe("verification", {"verified": verified, "reason": "model_stop"})
                if not verified:
                    return self._finish(False)
                self._phase(AgentPhase.REFLECT, "Capture a short reflection.")
                self._reflect()
                self._observe("reflection", {"files_touched": self.state.files_touched})
                return self._finish(True)

            if decision.action is None:
                self.failure_reason = "model did not provide an action"
                self.state.task_state.failed_attempts.append(self.failure_reason)
                return self._finish(False)

            self._phase(AgentPhase.ACT, "Execute model-requested tool action.")
            try:
                action_result = self._act(decision.action.tool, decision.action.arguments)
            except RuntimeError as exc:
                self.failure_reason = str(exc)
                self.state.task_state.failed_attempts.append(str(exc))
                return self._finish(False)
            self._observe("action_result", action_result)

            self._phase(AgentPhase.OBSERVE, "Feed tool observation into next model turn.")
            observation = {
                "tool": action_result["tool"],
                "ok": action_result["ok"],
                "result": action_result["result"],
                "output": action_result["output"],
                "error": action_result["error"],
                "metadata": action_result["metadata"],
                "truncated": action_result["truncated"],
                "turn": turn_index,
            }
            self.observations.append(observation)
            self._observe("agent_observed", observation)

            if action_result["ok"]:
                failed_tool_attempts = 0
                self.failure_reason = None
                continue

            failed_tool_attempts += 1
            self.state.metrics.retries += 1
            self.state.task_state.failed_attempts.append(str(action_result["result"]))
            if failed_tool_attempts > self.max_failed_tool_attempts:
                self.failure_reason = "tool failed too many times"
                self.state.task_state.failed_attempts.append(self.failure_reason)
                return self._finish(False)
            self._phase(AgentPhase.PLAN, "Replan after tool failure.")

        self.failure_reason = "max agent steps exceeded"
        self.state.task_state.failed_attempts.append(self.failure_reason)
        return self._finish(False)

    def _load_context(self) -> list[str]:
        observation = self._execute_tool("list_files", {})
        if not observation.ok:
            self.failure_reason = observation.error
            return []
        return [line.rstrip("/") for line in observation.output.splitlines() if line]

    def _act(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        context = ToolContext(workspace=self.runtime.workspace)
        observation = self._execute_tool(tool, arguments)
        result = self._tool_result(tool, observation)
        if observation.ok:
            path = arguments.get("path")
            if path:
                self.state.files_touched.append(str(path))
                self.state.task_state.files_relevant = [str(path)]
            return result

        self.failure_reason = observation.error
        return result

    def _tool_result(self, tool: str, observation: ToolObservation) -> dict[str, Any]:
        output = truncate_text(observation.output, MAX_OBSERVATION_CHARS)
        error = truncate_text(observation.error or "", MAX_OBSERVATION_CHARS) or None
        result = output if observation.ok else error
        return {
            "tool": tool,
            "ok": observation.ok,
            "result": result or "",
            "output": output,
            "error": error,
            "metadata": observation.metadata,
            "truncated": observation.truncated or len(observation.output) > MAX_OBSERVATION_CHARS,
        }

    def _verify(self, action_result: dict[str, Any]) -> bool:
        self.state.metrics.retries += 0
        ok = bool(action_result.get("ok"))
        if not ok:
            self.state.task_state.failed_attempts.append(str(action_result.get("result")))
        return ok

    def _verify_model_stop(self, decision: ModelDecision) -> bool:
        if not decision.final_answer or not decision.final_answer.strip():
            self.failure_reason = "model stop missing final_answer"
            self.state.task_state.failed_attempts.append(self.failure_reason)
            return False
        self.state.task_state.decisions.append(decision.final_answer)
        return True

    def _reflect(self) -> None:
        self.state.task_state.decisions.append("Keep the first loop minimal and traceable.")

    def _plan_action(self, known_files: list[str]) -> PlannedAction | None:
        self.state.task_state.next_actions = self.rule_planner.plan_steps(self.state.user_goal)
        return self.rule_planner.next_action(self.state.user_goal, known_files)

    def _plan_model_decision(
        self,
        known_files: list[str],
        turn_index: int,
        failed_tool_attempts: int,
    ) -> ModelDecision | None:
        try:
            decision = self.model_planner.plan(
                self.state.user_goal,
                known_files,
                observations=self.observations,
                turn_index=turn_index,
                failed_tool_attempts=failed_tool_attempts,
            )
        except Exception as exc:
            self.failure_reason = str(exc)
            self.state.task_state.failed_attempts.append(str(exc))
            self._observe("planning_failed", {"reason": str(exc), "planner": "model", "turn": turn_index})
            return None
        if decision.selected_skill:
            self.state.selected_skills = [decision.selected_skill]
        self.state.task_state.next_actions = decision.next_actions
        self.state.metrics.input_tokens += decision.input_tokens
        self.state.metrics.output_tokens += decision.output_tokens
        return decision

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


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n[truncated]"
