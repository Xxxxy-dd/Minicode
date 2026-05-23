from dataclasses import dataclass
from typing import Any

from minicode_agent.config import normalize_memory_reflection_mode
from minicode_agent.context import TaskStateCompressor
from minicode_agent.core.state import AgentPhase, AgentState, RunMetrics, TaskState
from minicode_agent.agent.planner import ModelDecision, ModelDrivenPlanner, PlannedAction, RuleBasedPlanner
from minicode_agent.memory import DeterministicReflectionEngine, LLMReflectionEngine, MemoryRecord, MemoryReflectionResult
from minicode_agent.models import ModelClient
from minicode_agent.runtime import RuntimeContext
from minicode_agent.skills import SkillDefinition, SkillError, SkillRouter, default_skill_registry
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext, ToolObservation


MAX_OBSERVATION_CHARS = 4000
SINGLE_OBSERVATION_COMPRESSION_CHARS = 3000
RECENT_OBSERVATIONS_COMPRESSION_CHARS = 5000
TOTAL_HISTORY_COMPRESSION_CHARS = 8000


@dataclass
class AgentRunResult:
    state: AgentState
    transcript: list[dict[str, Any]]


class AgentLoop:
    """MiniCode V1 coding-agent loop with tools, skills, memory, compression, and subagents."""

    def __init__(
        self,
        runtime: RuntimeContext,
        goal: str,
        max_steps: int = 30,
        max_failed_tool_attempts: int = 2,
        model_client: ModelClient | None = None,
        aux_model_client: ModelClient | None = None,
        enable_skills: bool = True,
        enable_skill_rerank: bool = False,
        enable_memory: bool = True,
        enable_compression: bool = True,
        enable_subagents: bool = True,
        memory_reflection_mode: str = "deterministic",
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
        self.enable_skills = enable_skills
        self.enable_skill_rerank = enable_skill_rerank
        self.enable_memory = enable_memory
        self.enable_compression = enable_compression
        self.enable_subagents = enable_subagents
        self.memory_reflection_mode = normalize_memory_reflection_mode(memory_reflection_mode)
        self.aux_model_client = aux_model_client or model_client
        registry = create_default_registry(include_subagents=enable_subagents)
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
        self.skill_registry = default_skill_registry(runtime.workspace)
        self.skill_router = SkillRouter(
            self.skill_registry,
            model_client=self.aux_model_client,
            enable_llm_rerank=enable_skill_rerank,
        )
        self.active_skills: list[SkillDefinition] = []
        self.reflection_engine = DeterministicReflectionEngine()
        self.llm_reflection_engine = (
            LLMReflectionEngine(self.aux_model_client, self.reflection_engine) if self.aux_model_client else None
        )
        self.active_memories: list[MemoryRecord] = []
        self.compressor = TaskStateCompressor()

    def run(self) -> AgentRunResult:
        self.runtime.trace_store.append(
            self.runtime.run_id,
            "run_started",
            {
                "goal": self.state.user_goal,
                "workspace": self.state.workspace,
                "max_steps": self.max_steps,
                "features": self._feature_flags(),
            },
        )
        self._phase(AgentPhase.INIT, "Initialize agent state.")

        self._phase(AgentPhase.LOAD_CONTEXT, "Load workspace rules and trace context.")
        known_files = self._load_context()
        self.active_memories = self.runtime.memory_store.search(self.state.user_goal) if self.enable_memory else []
        self._observe(
            "context_loaded",
            {
                "workspace": self.state.workspace,
                "known_files": known_files[:20],
                "memory_count": len(self.active_memories),
            },
        )

        self._phase(AgentPhase.SELECT_SKILL, "Select a skill for the task.")
        if self.enable_skills:
            route_result = self.skill_router.route(self.state.user_goal)
            self.state.selected_skills = route_result.selected
            self.state.skill_candidates = [
                {"name": candidate.name, "score": candidate.score, "reasons": candidate.reasons}
                for candidate in route_result.candidates
            ]
            self.state.skill_route_reasons = route_result.reasons
            if self.state.selected_skills:
                self._load_active_skills()
            if route_result.rerank_used:
                self.state.metrics.skill_rerank_calls += 1
                if route_result.rerank_fallback:
                    self.state.metrics.skill_rerank_fallbacks += 1
                self._observe(
                    "skill_reranked",
                    {
                        "selected": self.state.selected_skills,
                        "fallback": route_result.rerank_fallback,
                        "reason": route_result.rerank_reason,
                    },
                )
        else:
            self.state.selected_skills = []
            self.state.skill_candidates = []
            self.state.skill_route_reasons = {}
        self._observe(
            "skill_selected",
            {
                "skills": self.state.selected_skills,
                "candidates": self.state.skill_candidates,
                "reasons": self.state.skill_route_reasons,
                "rerank_used": route_result.rerank_used if self.enable_skills else False,
                "rerank_fallback": route_result.rerank_fallback if self.enable_skills else False,
                "rerank_reason": route_result.rerank_reason if self.enable_skills else None,
            },
        )

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
                "features": self._feature_flags(),
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
                "id": f"obs_{turn_index}",
            }
            self.observations.append(observation)
            self._observe("agent_observed", observation)
            self._maybe_compress_context()

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
        if tool == "spawn_subagent" and observation.ok:
            self.state.metrics.subagent_calls += 1
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
        if not self.enable_memory or self.memory_reflection_mode == "off":
            self._observe("memory_reflected", self._memory_stats(0, 0, 0, mode="off"))
            return
        reflection: MemoryReflectionResult
        if self.memory_reflection_mode == "llm" and self.llm_reflection_engine:
            reflection = self.llm_reflection_engine.generate_with_fallback(self.state, self.transcript)
        else:
            candidates = self.reflection_engine.generate(self.state, self.transcript)
            reflection = MemoryReflectionResult(
                summary=self.reflection_engine.summarize(self.state, candidates),
                candidates=candidates,
                filtered_count=0,
                fallback_used=self.memory_reflection_mode == "llm",
                fallback_reason="model client unavailable" if self.memory_reflection_mode == "llm" else None,
            )
        if self.memory_reflection_mode == "llm":
            self.state.metrics.memory_llm_calls += 1
            if reflection.fallback_used:
                self.state.metrics.memory_llm_fallbacks += 1
            self._observe(
                "memory_llm_requested",
                {
                    "fallback": reflection.fallback_used,
                    "reason": reflection.fallback_reason,
                    "filtered": reflection.filtered_count,
                    "summary": reflection.summary,
                },
            )
        written = 0
        skipped = 0
        rejected_reasons: dict[str, int] = {}
        duplicates = 0
        for candidate in reflection.candidates:
            record, admission_reason = self.reflection_engine.admit(candidate)
            if record is None:
                skipped += 1
                rejected_reasons[admission_reason] = rejected_reasons.get(admission_reason, 0) + 1
                self._observe("memory_rejected", {"kind": candidate.kind.value, "reason": admission_reason})
                continue
            try:
                _, inserted = self.runtime.memory_store.add(
                    record.kind,
                    record.content,
                    confidence=record.confidence,
                    source_run_id=record.source_run_id,
                    tags=record.tags,
                    reason=record.reason,
                    metadata=record.metadata,
                )
            except ValueError as exc:
                skipped += 1
                rejected_reasons[str(exc)] = rejected_reasons.get(str(exc), 0) + 1
                self._observe("memory_rejected", {"kind": record.kind.value, "reason": str(exc)})
                continue
            if inserted:
                written += 1
                self.runtime.trace_store.append(
                    self.runtime.run_id,
                    "memory_written",
                    {
                        "kind": record.kind.value,
                        "confidence": record.confidence,
                        "reason": record.reason,
                        "tags": record.tags,
                    },
                )
            else:
                duplicates += 1
                skipped += 1
        self.state.metrics.memory_llm_filtered += reflection.filtered_count
        if reflection.summary:
            self.state.task_state.history_summary = reflection.summary
        self._observe(
            "memory_reflected",
            self._memory_stats(
                len(reflection.candidates),
                written,
                skipped,
                duplicates,
                rejected_reasons,
                reflection.filtered_count,
                reflection.summary,
            ),
        )

    def _plan_action(self, known_files: list[str]) -> PlannedAction | None:
        self.state.task_state.next_actions = self.rule_planner.plan_steps(self.state.user_goal)
        planned = self.rule_planner.next_action(self.state.user_goal, known_files)
        if planned.tool == "spawn_subagent" and not self.enable_subagents:
            if "README.md" in known_files:
                return PlannedAction(
                    tool="read_file",
                    arguments={"path": "README.md"},
                    description="Inspect README.md because subagents are disabled by the eval config.",
                )
            return PlannedAction(
                tool="list_files",
                arguments={},
                description="List workspace files because subagents are disabled by the eval config.",
            )
        return planned

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
                skills=self.active_skills,
                memories=self.active_memories,
                task_state=self.state.task_state,
                turn_index=turn_index,
                failed_tool_attempts=failed_tool_attempts,
            )
        except Exception as exc:
            self.failure_reason = str(exc)
            self.state.task_state.failed_attempts.append(str(exc))
            self._observe("planning_failed", {"reason": str(exc), "planner": "model", "turn": turn_index})
            return None
        if self.enable_skills and decision.selected_skill:
            self.state.selected_skills = [decision.selected_skill]
            self._load_active_skills()
        self.state.task_state.next_actions = decision.next_actions
        self.state.metrics.input_tokens += decision.input_tokens
        self.state.metrics.output_tokens += decision.output_tokens
        return decision

    def _maybe_compress_context(self) -> None:
        if not self.enable_compression:
            return
        if not self.observations:
            return
        single_chars = len(observation_body(self.observations[-1]))
        recent_chars = sum(len(observation_body(observation)) for observation in self.observations[-3:])
        total_chars = sum(len(observation_body(observation)) for observation in self.observations)
        should_compress = (
            single_chars >= SINGLE_OBSERVATION_COMPRESSION_CHARS
            or recent_chars >= RECENT_OBSERVATIONS_COMPRESSION_CHARS
            or total_chars >= TOTAL_HISTORY_COMPRESSION_CHARS
        )
        if not should_compress:
            return

        self._phase(AgentPhase.COMPRESS_CONTEXT, "Compress long recent observations into structured task state.")
        recent = self.observations[-3:] if total_chars < TOTAL_HISTORY_COMPRESSION_CHARS else self.observations
        try:
            result = self.compressor.compress(self.state.task_state, recent)
        except Exception as exc:
            result = self.compressor.fallback_compress(self.state.task_state, recent, str(exc))
        self.state.task_state = result.task_state
        self.state.metrics.compression_events += 1
        self.state.metrics.compression_input_chars += result.input_chars
        self.state.metrics.compression_output_chars += result.output_chars
        self.state.metrics.compression_ratio_avg = round(
            self.state.metrics.compression_output_chars / self.state.metrics.compression_input_chars,
            4,
        )
        self.runtime.trace_store.append(
            self.runtime.run_id,
            "context_compressed",
            {
                "input_chars": result.input_chars,
                "output_chars": result.output_chars,
                "ratio": result.ratio,
                "fallback_used": result.fallback_used,
                "compressed_observations": result.compressed_observations,
                "compressed_observation_ids": result.compressed_observation_ids,
                "compressed_turns": result.compressed_turns,
                "task_state": result.task_state.model_dump(),
            },
        )
        self.observations[-3:] = [
            {
                "tool": "context_compressor",
                "ok": True,
                "result": result.summary,
                "output": result.summary,
                "error": None,
                "metadata": {
                    "input_chars": result.input_chars,
                    "output_chars": result.output_chars,
                    "ratio": result.ratio,
                    "fallback_used": result.fallback_used,
                },
                "truncated": False,
                "id": "compressed_" + "_".join(result.compressed_observation_ids or ["observations"]),
            }
        ]
        self._phase(AgentPhase.PLAN, "Continue planning with compressed context.")

    def _load_active_skills(self) -> None:
        active: list[SkillDefinition] = []
        for skill_name in self.state.selected_skills:
            try:
                active.append(self.skill_registry.get(skill_name))
            except SkillError as exc:
                self.failure_reason = str(exc)
                self.state.task_state.failed_attempts.append(str(exc))
                self._observe("skill_load_failed", {"skill": skill_name, "reason": str(exc)})
        self.active_skills = active

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
        if name == "spawn_subagent" and not self.enable_subagents:
            self.failure_reason = "subagents disabled by eval config"
            raise RuntimeError(self.failure_reason)
        if self.steps >= self.max_steps:
            self.failure_reason = "max agent steps exceeded"
            raise RuntimeError(self.failure_reason)
        self.steps += 1
        observation = self.executor.execute(name, ToolContext(workspace=self.runtime.workspace), arguments)
        self.state.metrics.tool_calls += 1
        if not observation.ok and observation.metadata.get("permission") == "deny":
            self.state.metrics.permission_blocks += 1
        return observation

    def _feature_flags(self) -> dict[str, bool | str]:
        return {
            "skills": self.enable_skills,
            "skill_rerank": self.enable_skill_rerank,
            "memory": self.enable_memory,
            "compression": self.enable_compression,
            "subagents": self.enable_subagents,
            "memory_reflection_mode": self.memory_reflection_mode,
        }

    def _memory_stats(
        self,
        candidates: int,
        written: int,
        skipped: int,
        duplicates: int = 0,
        rejected_reasons: dict[str, int] | None = None,
        filtered: int = 0,
        summary: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        self.state.metrics.memory_candidates += candidates
        self.state.metrics.memory_written += written
        self.state.metrics.memory_rejected += skipped
        self.state.metrics.memory_duplicates += duplicates
        return {
            "candidates": candidates,
            "written": written,
            "skipped": skipped,
            "duplicates": duplicates,
            "rejected_reasons": rejected_reasons or {},
            "filtered": filtered,
            "summary": summary,
            "mode": mode or self.memory_reflection_mode,
        }


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n[truncated]"


def observation_body(observation: dict[str, Any]) -> str:
    return str(observation.get("output") or observation.get("result") or observation.get("error") or "")
