import json
import subprocess

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.app import app
from minicode_agent.core.state import AgentPhase
from minicode_agent.runtime import RuntimeContext
from minicode_agent.subagents import SubagentRequest, SubagentRole, SubagentRunner
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext


def test_registry_includes_spawn_subagent() -> None:
    names = {tool.spec.name for tool in create_default_registry().list()}

    assert "spawn_subagent" in names


def test_explorer_subagent_reads_limited_context(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\nneedle\n", encoding="utf-8")
    runner = SubagentRunner(tmp_path)

    result = runner.run(
        SubagentRequest(
            role=SubagentRole.EXPLORER,
            task="find needle",
            pattern="needle",
            max_steps=3,
        )
    )

    assert result.ok
    assert result.role == SubagentRole.EXPLORER
    assert result.tool_calls == 1
    assert "README.md" in result.findings[0]
    assert "search_code" in result.allowed_tools
    assert "spawn_subagent" in result.denied_tools


def test_explorer_subagent_supports_path_and_pattern(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\nneedle\n", encoding="utf-8")

    result = SubagentRunner(tmp_path).run(
        SubagentRequest(
            role=SubagentRole.EXPLORER,
            task="read and find needle",
            path="README.md",
            pattern="needle",
            max_steps=3,
        )
    )

    assert result.tool_calls == 2
    assert result.findings[0].startswith("read_file:")
    assert result.findings[1].startswith("search_code:")


def test_reviewer_subagent_reviews_git_diff(tmp_path) -> None:
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

    result = SubagentRunner(tmp_path).run(
        SubagentRequest(role=SubagentRole.REVIEWER, task="review current diff", max_steps=2)
    )

    assert result.ok
    assert result.tool_calls == 2
    assert "hello again" in "\n".join(result.findings)
    assert result.allowed_tools == ["git_diff", "git_status", "read_file", "search_code"]
    assert "spawn_subagent" in result.denied_tools
    assert result.changed_files == ["tracked.txt"]
    assert result.test_suggestions == ["Run the relevant test suite before merging."]


def test_subagent_respects_max_steps(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    result = SubagentRunner(tmp_path).run(
        SubagentRequest(role=SubagentRole.EXPLORER, task="inspect project", max_steps=1)
    )

    assert result.tool_calls == 1
    assert result.stopped_reason == "max_steps"


def test_spawn_subagent_cannot_use_write_tools(tmp_path) -> None:
    executor = ToolExecutor(create_default_registry(), run_id="subagent_permission_test")

    observation = executor.execute(
        "spawn_subagent",
        ToolContext(workspace=tmp_path),
        {"role": "explorer", "task": "try to write", "tool": "write_file", "max_steps": 1},
    )

    assert observation.ok
    payload = json.loads(observation.output)
    assert payload["tool_calls"] == 1
    assert "write_file" not in payload["allowed_tools"]
    assert "spawn_subagent" in payload["denied_tools"]
    assert not (tmp_path / "notes.txt").exists()


def test_spawn_subagent_tool_metadata_includes_role_limits(tmp_path) -> None:
    executor = ToolExecutor(create_default_registry(), run_id="subagent_metadata_test")

    observation = executor.execute(
        "spawn_subagent",
        ToolContext(workspace=tmp_path),
        {"role": "explorer", "task": "inspect project", "max_steps": 1},
    )

    assert observation.ok
    assert observation.metadata["role"] == "explorer"
    assert observation.metadata["max_steps"] == 1
    assert "list_files" in observation.metadata["allowed_tools"]
    assert "spawn_subagent" in observation.metadata["denied_tools"]


def test_cli_tools_run_spawn_subagent(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    from typer.testing import CliRunner

    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "spawn_subagent",
            "--workspace",
            str(tmp_path),
            "--role",
            "explorer",
            "--task",
            "inspect project",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"role": "explorer"' in result.output
    assert '"tool_calls"' in result.output


def test_agent_loop_review_task_uses_reviewer_subagent(tmp_path) -> None:
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
    runtime = RuntimeContext.create(tmp_path, run_id="agent_subagent_test")

    result = AgentLoop(runtime, "review current diff").run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.subagent_calls == 1
    events = runtime.trace_store.list_events("agent_subagent_test")
    event_types = [event.event_type for event in events]
    assert "subagent_started" in event_types
    assert "subagent_finished" in event_types


def test_agent_loop_does_not_trigger_reviewer_for_generic_review_word(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_generic_review_test")

    result = AgentLoop(runtime, "review README style").run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.subagent_calls == 0
