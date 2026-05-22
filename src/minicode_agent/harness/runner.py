from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from csv import DictWriter
from datetime import UTC, datetime
from pathlib import Path

from minicode_agent.agent import AgentLoop
from minicode_agent.harness.configs import AblationConfig, ablation_config_names, load_ablation_config_file, resolve_ablation_config
from minicode_agent.harness.types import EvalResult, HarnessTask, SuccessCommand, SuccessResult
from minicode_agent.runtime import RuntimeContext


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
        self.eval_id = datetime.now(UTC).strftime("eval_%Y%m%d_%H%M%S")

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
        runtime = RuntimeContext.create(workspace, run_kind="eval")
        result = AgentLoop(runtime, task.prompt, **self.ablation_config.agent_kwargs()).run()
        success_results = [run_success_command(command, workspace) for command in task.success]
        agent_ok = result.state.current_phase.value == "done"
        command_passed = all(success.passed for success in success_results)
        passed = evaluate_outcome(task, agent_ok, command_passed)
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
                "success_count": len(success_results),
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
            success_results=success_results,
            metrics=result.state.metrics.model_dump(),
            config_features=self.ablation_config.model_dump(),
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


def run_all_configs(root: Path, taskset: Path) -> tuple[list[EvalResult], Path]:
    all_results: list[EvalResult] = []
    report_paths: list[Path] = []
    eval_id = datetime.now(UTC).strftime("eval_%Y%m%d_%H%M%S")
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
        f"- passed: {passed}",
        f"- pass_rate: {pass_rate:.2%}",
        "",
        "| Task | Category | Expected | Passed | Runtime | Tool Calls | Retries | Compression | Subagents | Memory Written | Memory Rejected | Trace |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
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
        lines.append("")
    return "\n".join(lines)


def render_comparison_report(results: list[EvalResult], report_paths: list[Path]) -> str:
    grouped: dict[str, list[EvalResult]] = {}
    for result in results:
        grouped.setdefault(result.config, []).append(result)
    lines = [
        "# MiniCode Ablation Comparison",
        "",
        "| Config | Tasks | Passed | Pass Rate | Avg Runtime | Tool Calls | Retries | Compression | Subagents | Memory Mode |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for config_name in ablation_config_names():
        group = grouped.get(config_name, [])
        total = len(group)
        passed = sum(1 for result in group if result.passed)
        pass_rate = (passed / total) if total else 0
        avg_runtime = (sum(result.runtime_seconds for result in group) / total) if total else 0
        tool_calls = sum(int(result.metrics.get("tool_calls", 0)) for result in group)
        retries = sum(int(result.metrics.get("retries", 0)) for result in group)
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
            f"tool_calls {delta_tools:+.0f}, memory_written {summary['memory_written']:.0f}, "
            f"subagent_calls {summary['subagent_calls']:.0f}."
        )
    notes.append(
        "- LLM memory configs call the LLM reflection engine only when a model client is available; otherwise they fall back to deterministic reflection and record the fallback reason."
    )
    return notes


def summarize_group(results: list[EvalResult]) -> dict[str, float]:
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    return {
        "pass_rate": (passed / total) if total else 0.0,
        "tool_calls": float(sum(int(result.metrics.get("tool_calls", 0)) for result in results)),
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
        "compression_events",
        "subagent_calls",
        "memory_candidates",
        "memory_written",
        "memory_rejected",
        "memory_duplicates",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            metrics = result.metrics
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
                    "compression_events": metrics.get("compression_events", 0),
                    "subagent_calls": metrics.get("subagent_calls", 0),
                    "memory_candidates": metrics.get("memory_candidates", 0),
                    "memory_written": metrics.get("memory_written", 0),
                    "memory_rejected": metrics.get("memory_rejected", 0),
                    "memory_duplicates": metrics.get("memory_duplicates", 0),
                }
            )


def evaluate_outcome(task: HarnessTask, agent_ok: bool, command_passed: bool) -> bool:
    if task.expected == "analysis_only":
        return agent_ok
    if task.expected == "fail":
        return agent_ok and not command_passed
    return agent_ok and command_passed


def summarize_stream(value: str, max_chars: int = 300) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + " [truncated]"
