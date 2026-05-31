import json
import sys
import subprocess

from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.harness import ForbiddenToolAssertion, HarnessRunner, HarnessTask, SuccessCommand, TeamAssertion, TraceAssertion
from minicode_agent.harness.runner import run_success_command


def write_task(path, workspace, command: str, task_id: str = "task_1") -> None:
    path.write_text(
        json.dumps(
            {
                "id": task_id,
                "workspace": str(workspace),
                "prompt": "inspect project",
                "expected": "pass",
                "category": "smoke",
                "tags": ["test"],
                "difficulty": "easy",
                "success": [{"command": command, "exit_code": 0, "timeout_seconds": 30}],
            }
        ),
        encoding="utf-8",
    )


def test_harness_loads_single_task(tmp_path) -> None:
    task_path = tmp_path / "task.json"
    write_task(task_path, tmp_path, "python --version")

    tasks = HarnessRunner(tmp_path).load_tasks(task_path)

    assert len(tasks) == 1
    assert tasks[0].id == "task_1"
    assert tasks[0].workspace == tmp_path
    assert tasks[0].category == "smoke"
    assert tasks[0].tags == ["test"]


def test_harness_loads_task_directory(tmp_path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    write_task(tasks_dir / "a.json", tmp_path, "python --version", task_id="a")
    write_task(tasks_dir / "b.json", tmp_path, "python --version", task_id="b")

    tasks = HarnessRunner(tmp_path).load_tasks(tasks_dir)

    assert [task.id for task in tasks] == ["a", "b"]


def test_run_success_command_passes_and_fails(tmp_path) -> None:
    passed = run_success_command(SuccessCommand(command=f'"{sys.executable}" --version'), tmp_path)
    failed = run_success_command(SuccessCommand(command=f'"{sys.executable}" -c "import sys; sys.exit(3)"'), tmp_path)

    assert passed.passed
    assert not failed.passed
    assert failed.exit_code == 3


def test_harness_run_task_collects_metrics_and_trace(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task = HarnessTask(
        id="inspect",
        workspace=tmp_path,
        prompt="inspect project",
        success=[SuccessCommand(command=f'"{sys.executable}" --version')],
    )

    result = HarnessRunner(tmp_path, config="baseline").run_task(task)

    assert result.passed
    assert result.agent_ok
    assert result.config == "baseline"
    assert result.source_workspace == str(tmp_path)
    assert result.workspace != str(tmp_path)
    assert result.metrics["tool_calls"] >= 1
    assert result.runtime_seconds >= 0
    assert result.trace_path
    assert (tmp_path / ".minicode" / "eval_workspaces" / "baseline").exists()


def test_harness_expected_fail_passes_when_command_fails(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task = HarnessTask(
        id="expected_fail",
        workspace=tmp_path,
        prompt="inspect project",
        expected="fail",
        success=[SuccessCommand(command=f'"{sys.executable}" -c "import sys; sys.exit(3)"')],
    )

    result = HarnessRunner(tmp_path).run_task(task)

    assert result.passed
    assert not result.success_results[0].passed


def test_harness_expected_fail_without_success_uses_assertions(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task = HarnessTask(
        id="expected_fail_without_success",
        workspace=tmp_path,
        prompt="inspect project",
        expected="fail",
        trace_assertions=[TraceAssertion(event_type="run_started")],
    )

    result = HarnessRunner(tmp_path).run_task(task)

    assert result.passed
    assert result.assertion_results[0].passed


def test_harness_analysis_only_ignores_success_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task = HarnessTask(
        id="analysis",
        workspace=tmp_path,
        prompt="inspect project",
        expected="analysis_only",
        success=[],
    )

    result = HarnessRunner(tmp_path).run_task(task)

    assert result.passed


def test_harness_analysis_only_requires_assertions(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task = HarnessTask(
        id="analysis_assertion_failure",
        workspace=tmp_path,
        prompt="inspect project",
        expected="analysis_only",
        trace_assertions=[TraceAssertion(event_type="missing_event")],
    )

    result = HarnessRunner(tmp_path).run_task(task)

    assert not result.passed
    assert result.assertion_results[0].kind == "trace"


def test_harness_trace_and_forbidden_tool_assertions(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task = HarnessTask(
        id="trace_assertions",
        workspace=tmp_path,
        prompt="inspect project",
        expected="analysis_only",
        trace_assertions=[TraceAssertion(event_type="run_started")],
        forbidden_tools=[ForbiddenToolAssertion(tool="write_file")],
    )

    result = HarnessRunner(tmp_path).run_task(task)

    assert result.passed
    assert {assertion.kind for assertion in result.assertion_results} == {"trace", "forbidden_tool"}


def test_harness_team_assertions(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "tracked.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "tracked.txt").write_text("hello again\n", encoding="utf-8")
    task = HarnessTask(
        id="team_assertions",
        workspace=tmp_path,
        prompt="Review current diff and report risks, changed files, and test suggestions.",
        expected="analysis_only",
        trace_assertions=[TraceAssertion(event_type="team_finished")],
        team_assertions=[TeamAssertion(role="reviewer", require_evidence=True, require_merge_blocker=True)],
    )

    result = HarnessRunner(tmp_path, config="full").run_task(task)

    assert result.passed
    assert all(assertion.passed for assertion in result.assertion_results)


def test_harness_team_assertion_fails_when_subagents_disabled(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "tracked.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "tracked.txt").write_text("hello again\n", encoding="utf-8")
    task = HarnessTask(
        id="team_disabled",
        workspace=tmp_path,
        prompt="Review current diff and report risks, changed files, and test suggestions.",
        expected="analysis_only",
        team_assertions=[TeamAssertion(role="reviewer", require_evidence=True, require_merge_blocker=True)],
    )

    result = HarnessRunner(tmp_path, config="baseline").run_task(task)

    assert not result.passed
    assert result.assertion_results[0].kind == "team"
    assert not result.assertion_results[0].passed


def test_harness_writes_markdown_report(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task = HarnessTask(
        id="inspect",
        workspace=tmp_path,
        prompt="inspect project",
        success=[SuccessCommand(command=f'"{sys.executable}" --version')],
    )
    runner = HarnessRunner(tmp_path, config="full")
    result = runner.run_task(task)

    report_path = runner.write_report([result])

    text = report_path.read_text(encoding="utf-8")
    assert "full" in str(report_path)
    assert "# MiniCode Eval Report" in text
    assert "config: full" in text
    assert "pass_rate" in text
    assert "stdout:" in text
    assert "inspect" in text


def test_cli_eval_runs_task_file(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task_path = tmp_path / "task.json"
    write_task(task_path, tmp_path, f'"{sys.executable}" --version')

    result = CliRunner().invoke(app, ["eval", str(task_path), "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "MiniCode Eval" in result.output
    assert "task_1" in result.output
    assert "report:" in result.output


def test_cli_eval_accepts_config(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task_path = tmp_path / "task.json"
    write_task(task_path, tmp_path, f'"{sys.executable}" --version')

    result = CliRunner().invoke(app, ["eval", str(task_path), "--workspace", str(tmp_path), "--config", "baseline"])

    assert result.exit_code == 0, result.output
    assert "baseline" in result.output or "report:" in result.output
