import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.app import app
from minicode_agent.core.state import AgentPhase
from minicode_agent.harness import HarnessRunner, ablation_config_names, load_ablation_config_file, resolve_ablation_config
from minicode_agent.harness.runner import render_comparison_report
from minicode_agent.harness.types import EvalResult
from minicode_agent.runtime import RuntimeContext


def test_ablation_config_presets_define_expected_feature_flags() -> None:
    baseline = resolve_ablation_config("baseline")
    full = resolve_ablation_config("full")
    full_llm = resolve_ablation_config("full_llm_memory")

    assert baseline.agent_kwargs() == {
        "enable_skills": False,
        "enable_skill_rerank": False,
        "enable_memory": False,
        "enable_compression": False,
        "enable_subagents": False,
        "memory_reflection_mode": "off",
    }
    assert full.enable_skills
    assert full.enable_skill_rerank
    assert full.enable_memory
    assert full.enable_compression
    assert full.enable_subagents
    assert full.memory_reflection_mode == "deterministic"
    assert full_llm.uses_llm_memory
    assert full_llm.enable_skill_rerank
    assert "baseline" in ablation_config_names()


def test_baseline_disables_skill_and_memory_events(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="ablation_baseline_test")

    result = AgentLoop(runtime, "add unit test", **resolve_ablation_config("baseline").agent_kwargs()).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.selected_skills == []
    assert runtime.memory_store.list() == []
    events = runtime.trace_store.list_events("ablation_baseline_test")
    event_types = [event.event_type for event in events]
    assert "memory_written" not in event_types
    assert "context_compressed" not in event_types


def test_skill_only_selects_skill_without_writing_memory(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="ablation_skill_test")

    result = AgentLoop(runtime, "add unit test", **resolve_ablation_config("skill_only").agent_kwargs()).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.selected_skills == ["test-writing"]
    assert runtime.memory_store.list() == []


def test_full_config_allows_reviewer_subagent(tmp_path) -> None:
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
    runtime = RuntimeContext.create(tmp_path, run_id="ablation_full_subagent_test")

    result = AgentLoop(runtime, "review current diff", **resolve_ablation_config("full").agent_kwargs()).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.subagent_calls == 1


def test_baseline_review_task_falls_back_when_subagents_disabled(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="ablation_review_baseline_test")

    result = AgentLoop(runtime, "review current diff", **resolve_ablation_config("baseline").agent_kwargs()).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.subagent_calls == 0
    planned = [event for event in result.transcript if event["event"] == "agent_planned"]
    assert planned[0]["payload"]["tool"] == "read_file"


def test_harness_report_includes_feature_flags(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runner = HarnessRunner(tmp_path, config="memory_skill")
    task = runner.load_task(write_task(tmp_path, task_id="inspect"))
    result = runner.run_task(task)

    report_path = runner.write_report([result])
    text = report_path.read_text(encoding="utf-8")

    assert result.config_features["enable_memory"]
    assert "- config: memory_skill" in text
    assert "- memory: True" in text
    assert "- memory_reflection_mode: deterministic" in text
    assert (report_path.parent / "results.json").exists()
    assert (report_path.parent / "summary.csv").exists()


def test_cli_eval_all_writes_comparison_report(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task_path = write_task(tmp_path, task_id="inspect")

    result = CliRunner().invoke(app, ["eval", str(task_path), "--workspace", str(tmp_path), "--config", "all"])

    assert result.exit_code == 0, result.output
    assert "baseline" in result.output
    assert "full_llm_memory" in result.output
    reports = sorted((tmp_path / ".minicode" / "evals" / "all").glob("*/report.md"))
    assert reports
    report_text = reports[-1].read_text(encoding="utf-8")
    assert "# MiniCode Ablation Comparison" in report_text
    assert "Experiment Notes" in report_text
    assert (reports[-1].parent / "results.json").exists()
    assert (reports[-1].parent / "summary.csv").exists()


def test_load_custom_ablation_config_file(tmp_path) -> None:
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(
            {
                "name": "custom_memory",
                "description": "Custom memory-only config.",
                "enable_memory": True,
                "memory_reflection_mode": "deterministic",
            }
        ),
        encoding="utf-8",
    )

    config = load_ablation_config_file(path)

    assert config.name == "custom_memory"
    assert config.enable_memory


def test_load_custom_ablation_config_rejects_invalid_memory_mode(tmp_path) -> None:
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(
            {
                "name": "custom_memory",
                "description": "Custom config.",
                "enable_memory": True,
                "memory_reflection_mode": "maybe",
            }
        ),
        encoding="utf-8",
    )

    try:
        load_ablation_config_file(path)
    except ValueError as exc:
        assert "memory_reflection_mode" in str(exc)
    else:
        raise AssertionError("expected invalid memory_reflection_mode to be rejected")


def test_cli_eval_lists_configs(tmp_path) -> None:
    result = CliRunner().invoke(app, ["eval", "examples/tasks", "--workspace", str(tmp_path), "--list-configs"])

    assert result.exit_code == 0, result.output
    assert "MiniCode Eval Configs" in result.output
    assert "baseline" in result.output
    assert "full_llm_memory" in result.output


def test_cli_eval_accepts_config_file(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    task_path = write_task(tmp_path, task_id="inspect")
    config_path = tmp_path / "custom.json"
    config_path.write_text(
        json.dumps(
            {
                "name": "custom_skill",
                "description": "Custom skill-only config.",
                "enable_skills": True,
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["eval", str(task_path), "--workspace", str(tmp_path), "--config-file", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "custom_skill" in result.output


def test_render_comparison_report_summarizes_configs() -> None:
    result = EvalResult(
        task_id="inspect",
        config="baseline",
        expected="pass",
        category="smoke",
        tags=[],
        difficulty="easy",
        prompt="inspect",
        source_workspace="src",
        workspace="work",
        run_id="run_1",
        passed=True,
        agent_ok=True,
        runtime_seconds=0.1,
        metrics={"tool_calls": 2, "retries": 0},
        config_features=resolve_ablation_config("baseline").model_dump(),
        trace_path="trace.db",
    )

    text = render_comparison_report([result], [])

    assert "| baseline | 1 | 1 | 100.00%" in text
    assert "full_llm_memory" in text


def write_task(tmp_path, task_id: str = "task") -> Path:
    task_path = tmp_path / f"{task_id}.json"
    task_path.write_text(
        json.dumps(
            {
                "id": task_id,
                "workspace": str(tmp_path),
                "prompt": "inspect project",
                "expected": "pass",
                "success": [{"command": f'"{sys.executable}" --version', "exit_code": 0}],
            }
        ),
        encoding="utf-8",
    )
    return task_path
