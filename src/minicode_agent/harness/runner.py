from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from csv import DictWriter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from minicode_agent.agent import AgentLoop
from minicode_agent.harness.assertions import evaluate_assertions
from minicode_agent.harness.configs import AblationConfig, ablation_config_names, load_ablation_config_file, resolve_ablation_config
from minicode_agent.harness.types import EvalResult, HarnessTask, SetupToolCall, SetupToolResult, SuccessCommand, SuccessResult
from minicode_agent.models import OpenAICompatibleClient
from minicode_agent.config import MiniCodeConfig
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext
from minicode_agent.trace.store import TraceEvent, TraceStore


class HarnessRunner:
    def __init__(
        self,
        root: Path | None = None,
        config: str = "default",
        ablation_config: AblationConfig | None = None,
    ) -> None:
        self.root = (root or Path.cwd()).expanduser().resolve()
        self.ablation_config = ablation_config or resolve_ablation_config(config)
        self.config = self.ablation_config.name
        self.eval_id = make_eval_id()

    def load_tasks(self, taskset: Path) -> list[HarnessTask]:
        target = taskset.expanduser()
        if not target.is_absolute():
            target = (self.root / target).resolve()
        if target.is_dir():
            return [self.load_task(path) for path in sorted(target.glob("*.json"))]
        return [self.load_task(target)]

    def load_task(self, path: Path) -> HarnessTask:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        task = HarnessTask.model_validate(payload)
        if not task.workspace.is_absolute():
            task = task.model_copy(update={"workspace": (path.parent / task.workspace).resolve()})
        return task

    def run_task(self, task: HarnessTask) -> EvalResult:
        started_at = time.perf_counter()
        workspace = self.prepare_workspace(task)
        setup_results = [run_success_command(command, workspace) for command in task.setup]
        runtime = RuntimeContext.create(workspace, run_kind="eval")
        setup_tool_results = run_setup_tools(task.setup_tools, runtime)
        aux_model_client = self._build_aux_model_client()
        result = AgentLoop(
            runtime,
            task.prompt,
            aux_model_client=aux_model_client,
            **self.ablation_config.agent_kwargs(),
        ).run()
        success_results = [run_success_command(command, workspace) for command in task.success]
        trace_events = runtime.trace_store.list_events(runtime.run_id)
        assertion_results = evaluate_assertions(task, workspace, trace_events)
        agent_ok = result.state.current_phase.value == "done"
        setup_passed = all(setup.passed for setup in setup_results)
        setup_tools_passed = all(setup.ok for setup in setup_tool_results)
        command_passed = all(success.passed for success in success_results)
        assertions_passed = all(assertion.passed for assertion in assertion_results)
        passed = setup_passed and setup_tools_passed and evaluate_outcome(task, agent_ok, command_passed, assertions_passed, has_success_commands=bool(task.success))
        runtime_seconds = round(time.perf_counter() - started_at, 3)
        runtime.trace_store.append(
            runtime.run_id,
            "eval_finished",
            {
                "task_id": task.id,
                "config": self.config,
                "expected": task.expected.value,
                "passed": passed,
                "runtime_seconds": runtime_seconds,
                "setup_count": len(setup_results),
                "setup_tool_count": len(setup_tool_results),
                "success_count": len(success_results),
                "assertion_count": len(assertion_results),
            },
        )
        return EvalResult(
            task_id=task.id,
            config=self.config,
            expected=task.expected,
            category=task.category,
            tags=task.tags,
            difficulty=task.difficulty,
            prompt=task.prompt,
            source_workspace=str(task.workspace),
            workspace=str(workspace),
            run_id=runtime.run_id,
            passed=passed,
            agent_ok=agent_ok,
            runtime_seconds=runtime_seconds,
            setup_results=setup_results,
            setup_tool_results=setup_tool_results,
            success_results=success_results,
            assertion_results=assertion_results,
            metrics=result.state.metrics.model_dump(),
            config_features=self.ablation_config.model_dump(),
            memory_summary=result.state.task_state.history_summary,
            trace_path=str(runtime.trace_store.storage_path),
        )

    def run(self, taskset: Path) -> tuple[list[EvalResult], Path]:
        results = [self.run_task(task) for task in self.load_tasks(taskset)]
        report_path = self.write_report(results)
        return results, report_path

    def write_report(self, results: list[EvalResult]) -> Path:
        output_dir = self.root / ".minicode" / "evals" / self.config / self.eval_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "report.md"
        report_path.write_text(render_report(results, self.ablation_config), encoding="utf-8")
        write_machine_reports(output_dir, results)
        return report_path

    def prepare_workspace(self, task: HarnessTask) -> Path:
        target = self.root / ".minicode" / "eval_workspaces" / self.config / self.eval_id / task.id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            task.workspace,
            target,
            ignore=shutil.ignore_patterns(".git", ".minicode", ".pytest_cache", "__pycache__"),
        )
        return target

    def _build_aux_model_client(self):
        config = MiniCodeConfig.from_env(self.root)
        if not config.model_name:
            return None
        return OpenAICompatibleClient(
            model=config.model_name,
            api_key=config.model_api_key,
            base_url=config.model_base_url,
        )


def run_success_command(command: SuccessCommand, workspace: Path) -> SuccessResult:
    command_text = command.command.replace("{python}", f'"{sys.executable}"')
    try:
        completed = subprocess.run(
            command_text,
            cwd=workspace,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=command.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SuccessResult(
            command=command_text,
            expected_exit_code=command.exit_code,
            exit_code=None,
            passed=False,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            error=f"timeout after {command.timeout_seconds}s",
            stdout_summary=summarize_stream(exc.stdout or ""),
            stderr_summary=summarize_stream(exc.stderr or ""),
        )
    return SuccessResult(
        command=command_text,
        expected_exit_code=command.exit_code,
        exit_code=completed.returncode,
        passed=completed.returncode == command.exit_code,
        stdout=completed.stdout[-2000:],
        stderr=completed.stderr[-2000:],
        stdout_summary=summarize_stream(completed.stdout),
        stderr_summary=summarize_stream(completed.stderr),
    )


def run_setup_tools(tool_calls: list[SetupToolCall], runtime: RuntimeContext) -> list[SetupToolResult]:
    if not tool_calls:
        return []
    executor = ToolExecutor(create_default_registry(), trace_store=runtime.trace_store, run_id=runtime.run_id)
    context = ToolContext(workspace=runtime.workspace)
    results: list[SetupToolResult] = []
    for call in tool_calls:
        observation = executor.execute(call.tool, context, substitute_python_placeholder(call.arguments), approved=call.approved)
        results.append(
            SetupToolResult(
                tool=call.tool,
                ok=observation.ok,
                error=observation.error,
                output_summary=summarize_stream(observation.output),
            )
        )
    return results


def substitute_python_placeholder(value):
    if isinstance(value, str):
        return value.replace("{python}", sys.executable)
    if isinstance(value, list):
        return [substitute_python_placeholder(item) for item in value]
    if isinstance(value, dict):
        return {key: substitute_python_placeholder(item) for key, item in value.items()}
    return value


def run_all_configs(root: Path, taskset: Path) -> tuple[list[EvalResult], Path]:
    all_results: list[EvalResult] = []
    report_paths: list[Path] = []
    eval_id = make_eval_id()
    for config_name in ablation_config_names():
        runner = HarnessRunner(root, config=config_name)
        runner.eval_id = eval_id
        results, report_path = runner.run(taskset)
        all_results.extend(results)
        report_paths.append(report_path)
    report_dir = root.expanduser().resolve() / ".minicode" / "evals" / "all" / eval_id
    report_dir.mkdir(parents=True, exist_ok=True)
    combined_path = report_dir / "report.md"
    combined_path.write_text(render_comparison_report(all_results, report_paths), encoding="utf-8")
    write_machine_reports(report_dir, all_results)
    return all_results, combined_path


def make_eval_id() -> str:
    timestamp = datetime.now(UTC).strftime("eval_%Y%m%d_%H%M%S_%f")
    return f"{timestamp}_{uuid4().hex[:6]}"


def render_report(results: list[EvalResult], config: AblationConfig | None = None) -> str:
    passed = sum(1 for result in results if result.passed)
    total = len(results)
    pass_rate = (passed / total) if total else 0
    config_name = config.name if config else (results[0].config if results else "default")
    features = config.model_dump() if config else (results[0].config_features if results else {})
    lines = [
        "# MiniCode Eval Report",
        "",
        f"- tasks: {total}",
        f"- config: {config_name}",
        f"- description: {features.get('description', '')}",
        f"- skills: {features.get('enable_skills', False)}",
        f"- memory: {features.get('enable_memory', False)}",
        f"- compression: {features.get('enable_compression', False)}",
        f"- subagents: {features.get('enable_subagents', False)}",
        f"- memory_reflection_mode: {features.get('memory_reflection_mode', 'off')}",
        f"- skill_rerank: {features.get('enable_skill_rerank', False)}",
        f"- passed: {passed}",
        f"- pass_rate: {pass_rate:.2%}",
        "",
        "| Task | Category | Expected | Passed | Runtime | Tool Calls | Retries | Skill Reranks | Memory LLM | Compression | Subagents | Memory Written | Memory Rejected | Trace |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        metrics = result.metrics
        lines.append(
            "| "
            + " | ".join(
                [
                    result.task_id,
                    result.category,
                    result.expected.value,
                    "yes" if result.passed else "no",
                    f"{result.runtime_seconds:.3f}s",
                    str(metrics.get("tool_calls", 0)),
                    str(metrics.get("retries", 0)),
                    str(metrics.get("skill_rerank_calls", 0)),
                    str(metrics.get("memory_llm_calls", 0)),
                    str(metrics.get("compression_events", 0)),
                    str(metrics.get("subagent_calls", 0)),
                    str(metrics.get("memory_written", 0)),
                    str(metrics.get("memory_rejected", 0)),
                    result.trace_path,
                ]
            )
            + " |"
        )
    lines.append("")
    for result in results:
        lines.extend(
            [
                f"## {result.task_id}",
                "",
                f"- prompt: {result.prompt}",
                f"- category: {result.category}",
                f"- tags: {', '.join(result.tags) or '(none)'}",
                f"- difficulty: {result.difficulty}",
                f"- expected: {result.expected.value}",
                f"- source_workspace: `{result.source_workspace}`",
                f"- workspace: `{result.workspace}`",
                f"- run_id: `{result.run_id}`",
                f"- agent_ok: {result.agent_ok}",
                f"- config_features: `{json.dumps(result.config_features, ensure_ascii=False)}`",
                f"- memory_summary: `{result.memory_summary or '(none)'}`",
            ]
        )
        for success in result.success_results:
            lines.append(
                f"- success: `{success.command}` -> {success.exit_code} "
                f"(expected {success.expected_exit_code}) passed={success.passed}"
            )
            if success.stdout_summary:
                lines.append(f"  - stdout: {success.stdout_summary}")
            if success.stderr_summary:
                lines.append(f"  - stderr: {success.stderr_summary}")
        for setup in result.setup_results:
            lines.append(
                f"- setup: `{setup.command}` -> {setup.exit_code} "
                f"(expected {setup.expected_exit_code}) passed={setup.passed}"
            )
            if setup.stdout_summary:
                lines.append(f"  - stdout: {setup.stdout_summary}")
            if setup.stderr_summary:
                lines.append(f"  - stderr: {setup.stderr_summary}")
        for setup_tool in result.setup_tool_results:
            lines.append(
                f"- setup_tool: `{setup_tool.tool}` ok={setup_tool.ok}"
                + (f" error={setup_tool.error}" if setup_tool.error else "")
            )
            if setup_tool.output_summary:
                lines.append(f"  - output: {setup_tool.output_summary}")
        for assertion in result.assertion_results:
            lines.append(f"- assertion[{assertion.kind}] `{assertion.target}` passed={assertion.passed}: {assertion.detail}")
        lines.extend(render_trace_evidence_summary(result))
        lines.append("")
    return "\n".join(lines)


def render_trace_evidence_summary(result: EvalResult) -> list[str]:
    events = load_trace_events(Path(result.trace_path), result.run_id)
    if not events:
        return []
    lines: list[str] = []
    memory_events = [event for event in events if event.event_type == "memory_recalled"]
    compression_events = [event for event in events if event.event_type == "context_compressed"]
    safety_events = [
        event
        for event in events
        if event.event_type == "injection_detected"
        or (
            event.event_type == "permission_checked"
            and permission_mode(event.payload) in {"ask", "deny"}
        )
    ]
    team_events = [event for event in events if event.event_type == "team_role_completed"]
    team_finished_events = [event for event in events if event.event_type == "team_finished"]
    worktree_events = [
        event
        for event in events
        if event.event_type in {"worktree_created", "worktree_retained", "worktree_cleanup_completed", "worktree_cleanup_failed"}
    ]
    if memory_events:
        lines.extend(["", "### Memory Evidence"])
        for event in memory_events:
            records = event.payload.get("records") or []
            if not records:
                lines.append(f"- memory_recalled: count={event.payload.get('count', 0)} records=(none)")
                continue
            for record in records[:8]:
                evidence = compact_json(record.get("evidence_refs") or [])
                lines.append(
                    "- memory_recalled: "
                    f"id={record.get('id')} kind={record.get('kind')} score={record.get('score')} "
                    f"reason={record.get('reason')} evidence_refs=`{evidence}`"
                )
    if safety_events:
        lines.extend(["", "### Safety Evidence"])
        for event in safety_events[:10]:
            if event.event_type == "injection_detected":
                evidence = event.payload.get("evidence") or {}
                lines.append(
                    "- injection_detected: "
                    f"source={event.payload.get('source')} trust={event.payload.get('trust_level')} "
                    f"rules={compact_json(evidence.get('rules') or event.payload.get('findings') or [])}"
                )
            else:
                mode = permission_mode(event.payload)
                lines.append(
                    "- permission_checked: "
                    f"tool={event.payload.get('tool')} mode={mode} "
                    f"reason={event.payload.get('reason') or permission_reason(event.payload)}"
                )
    if team_events or team_finished_events:
        lines.extend(["", "### Team Evidence"])
        for event in team_events[:10]:
            patch = event.payload.get("patch_proposal") or {}
            proposal_id = patch.get("proposal_id") if isinstance(patch, dict) else None
            lines.append(
                "- team_role_completed: "
                f"role={event.payload.get('role')} ok={event.payload.get('ok')} "
                f"tool_calls={event.payload.get('tool_calls')} proposal_id={proposal_id or '(none)'} "
                f"merge_blockers={compact_json(event.payload.get('merge_blockers') or [])} "
                f"evidence_refs={compact_json(event.payload.get('evidence_refs') or [])}"
            )
        for event in team_finished_events[-2:]:
            report = event.payload.get("team_report") or {}
            proposals = report.get("patch_proposals") or []
            lines.append(
                "- team_finished: "
                f"ok={event.payload.get('ok')} roles={event.payload.get('roles', [])} "
                f"patch_proposals={len(proposals)} "
                f"merge_blockers={compact_json(report.get('merge_blockers') or [])}"
            )
    if worktree_events:
        lines.extend(["", "### Worktree Evidence"])
        for event in worktree_events[:10]:
            lines.append(
                f"- {event.event_type}: "
                f"path={event.payload.get('worktree_path') or event.payload.get('suggested_worktree_path')} "
                f"branch={event.payload.get('created_branch')} "
                f"cleanup={event.payload.get('cleanup_policy')} "
                f"created={event.payload.get('created_worktree')} "
                f"will_merge={event.payload.get('will_merge')} "
                f"reason={event.payload.get('reason') or event.payload.get('error')}"
            )
    if compression_events:
        lines.extend(["", "### Context Compression Evidence"])
        for event in compression_events:
            frame = event.payload.get("context_frame") or {}
            lines.append(
                "- context_compressed: "
                f"ratio={event.payload.get('ratio')} compressed_ids={event.payload.get('compressed_observation_ids', [])} "
                f"evidence_refs=`{compact_json(event.payload.get('evidence_refs') or [])}`"
            )
            if frame:
                lines.append(
                    "  - context_frame: "
                    f"raw={frame.get('raw_observation_ids', [])} "
                    f"failures={len(frame.get('failure_refs') or [])} "
                    f"diffs={len(frame.get('diff_refs') or [])} "
                    f"tests={len(frame.get('test_refs') or [])}"
                )
    return lines


def load_trace_events(trace_path: Path, run_id: str) -> list[TraceEvent]:
    if not trace_path.exists():
        return []
    if trace_path.suffix == ".jsonl":
        return [
            event
            for event in (
                TraceEvent.model_validate_json(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if event.run_id == run_id
        ]
    try:
        return TraceStore(trace_path).list_events(run_id)
    except Exception:
        return []


def compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def permission_mode(payload: dict) -> str | None:
    decision = payload.get("decision")
    if isinstance(decision, dict):
        return decision.get("mode")
    if decision is not None:
        return str(decision)
    return None


def permission_reason(payload: dict) -> str | None:
    decision = payload.get("decision")
    if isinstance(decision, dict):
        return decision.get("reason")
    return None


def render_comparison_report(results: list[EvalResult], report_paths: list[Path]) -> str:
    grouped: dict[str, list[EvalResult]] = {}
    for result in results:
        grouped.setdefault(result.config, []).append(result)
    lines = [
        "# MiniCode Ablation Comparison",
        "",
        "| Config | Tasks | Passed | Pass Rate | Avg Runtime | Tool Calls | Retries | Skill Reranks | Memory LLM | Compression | Subagents | Memory Mode |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for config_name in ablation_config_names():
        group = grouped.get(config_name, [])
        total = len(group)
        passed = sum(1 for result in group if result.passed)
        pass_rate = (passed / total) if total else 0
        avg_runtime = (sum(result.runtime_seconds for result in group) / total) if total else 0
        tool_calls = sum(int(result.metrics.get("tool_calls", 0)) for result in group)
        retries = sum(int(result.metrics.get("retries", 0)) for result in group)
        skill_reranks = sum(int(result.metrics.get("skill_rerank_calls", 0)) for result in group)
        memory_llm = sum(int(result.metrics.get("memory_llm_calls", 0)) for result in group)
        compression = sum(int(result.metrics.get("compression_events", 0)) for result in group)
        subagents = sum(int(result.metrics.get("subagent_calls", 0)) for result in group)
        features = group[0].config_features if group else resolve_ablation_config(config_name).model_dump()
        lines.append(
            "| "
            + " | ".join(
                [
                    config_name,
                    str(total),
                    str(passed),
                    f"{pass_rate:.2%}",
                    f"{avg_runtime:.3f}s",
                    str(tool_calls),
                    str(retries),
                    str(skill_reranks),
                    str(memory_llm),
                    str(compression),
                    str(subagents),
                    str(features.get("memory_reflection_mode", "off")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Config Reports", ""])
    for path in report_paths:
        lines.append(f"- `{path}`")
    lines.extend(["", "## Experiment Notes", "", *experiment_notes(grouped)])
    return "\n".join(lines)


def experiment_notes(grouped: dict[str, list[EvalResult]]) -> list[str]:
    baseline = summarize_group(grouped.get("baseline", []))
    notes: list[str] = []
    for config_name in ablation_config_names():
        if config_name == "baseline":
            continue
        summary = summarize_group(grouped.get(config_name, []))
        delta_pass = summary["pass_rate"] - baseline["pass_rate"]
        delta_tools = summary["tool_calls"] - baseline["tool_calls"]
        notes.append(
            f"- `{config_name}` vs `baseline`: pass_rate {delta_pass:+.2%}, "
            f"tool_calls {delta_tools:+.0f}, skill_rerank_calls {summary['skill_rerank_calls']:.0f}, "
            f"memory_llm_calls {summary['memory_llm_calls']:.0f}, memory_written {summary['memory_written']:.0f}, "
            f"subagent_calls {summary['subagent_calls']:.0f}."
        )
    notes.append(
        "- LLM memory configs call the LLM reflection engine only when a model client is available; otherwise they fall back to deterministic reflection and record the fallback reason."
    )
    notes.append(
        "- Skill rerank runs on the auxiliary model channel only when enabled and a model client is available; otherwise routing stays deterministic."
    )
    return notes


def summarize_group(results: list[EvalResult]) -> dict[str, float]:
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    return {
        "pass_rate": (passed / total) if total else 0.0,
        "tool_calls": float(sum(int(result.metrics.get("tool_calls", 0)) for result in results)),
        "skill_rerank_calls": float(sum(int(result.metrics.get("skill_rerank_calls", 0)) for result in results)),
        "memory_llm_calls": float(sum(int(result.metrics.get("memory_llm_calls", 0)) for result in results)),
        "memory_written": float(sum(int(result.metrics.get("memory_written", 0)) for result in results)),
        "subagent_calls": float(sum(int(result.metrics.get("subagent_calls", 0)) for result in results)),
    }


def write_machine_reports(output_dir: Path, results: list[EvalResult]) -> None:
    payload = [result.model_dump(mode="json") for result in results]
    (output_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = output_dir / "summary.csv"
    fieldnames = [
        "config",
        "task_id",
        "passed",
        "expected",
        "category",
        "difficulty",
        "runtime_seconds",
        "tool_calls",
        "retries",
        "skill_rerank_calls",
        "memory_llm_calls",
        "compression_events",
        "subagent_calls",
        "memory_candidates",
        "memory_written",
        "memory_rejected",
        "memory_duplicates",
        "memory_llm_filtered",
        "memory_summary",
        "assertions_passed",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            metrics = result.metrics
            assertions_passed = all(assertion.passed for assertion in result.assertion_results)
            writer.writerow(
                {
                    "config": result.config,
                    "task_id": result.task_id,
                    "passed": result.passed,
                    "expected": result.expected.value,
                    "category": result.category,
                    "difficulty": result.difficulty,
                    "runtime_seconds": result.runtime_seconds,
                    "tool_calls": metrics.get("tool_calls", 0),
                    "retries": metrics.get("retries", 0),
                    "skill_rerank_calls": metrics.get("skill_rerank_calls", 0),
                    "memory_llm_calls": metrics.get("memory_llm_calls", 0),
                    "compression_events": metrics.get("compression_events", 0),
                    "subagent_calls": metrics.get("subagent_calls", 0),
                    "memory_candidates": metrics.get("memory_candidates", 0),
                    "memory_written": metrics.get("memory_written", 0),
                    "memory_rejected": metrics.get("memory_rejected", 0),
                    "memory_duplicates": metrics.get("memory_duplicates", 0),
                    "memory_llm_filtered": metrics.get("memory_llm_filtered", 0),
                    "memory_summary": result.memory_summary or "",
                    "assertions_passed": assertions_passed,
                }
            )


def evaluate_outcome(
    task: HarnessTask,
    agent_ok: bool,
    command_passed: bool,
    assertions_passed: bool = True,
    has_success_commands: bool = True,
) -> bool:
    if task.expected == "analysis_only":
        return agent_ok and assertions_passed
    if task.expected == "fail":
        if not has_success_commands:
            return agent_ok and assertions_passed
        return agent_ok and not command_passed and assertions_passed
    return agent_ok and command_passed and assertions_passed


def summarize_stream(value: str, max_chars: int = 300) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + " [truncated]"
