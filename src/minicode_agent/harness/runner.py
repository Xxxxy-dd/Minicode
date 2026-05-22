from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from minicode_agent.agent import AgentLoop
from minicode_agent.harness.types import EvalResult, HarnessTask, SuccessCommand, SuccessResult
from minicode_agent.runtime import RuntimeContext


class HarnessRunner:
    def __init__(self, root: Path | None = None, config: str = "default") -> None:
        self.root = (root or Path.cwd()).expanduser().resolve()
        self.config = config
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
        result = AgentLoop(runtime, task.prompt).run()
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
        report_path.write_text(render_report(results), encoding="utf-8")
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


def render_report(results: list[EvalResult]) -> str:
    passed = sum(1 for result in results if result.passed)
    total = len(results)
    pass_rate = (passed / total) if total else 0
    lines = [
        "# MiniCode Eval Report",
        "",
        f"- tasks: {total}",
        f"- config: {results[0].config if results else 'default'}",
        f"- passed: {passed}",
        f"- pass_rate: {pass_rate:.2%}",
        "",
        "| Task | Category | Expected | Passed | Runtime | Tool Calls | Retries | Compression | Subagents | Trace |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
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
