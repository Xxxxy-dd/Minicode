from typer.testing import CliRunner

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.app import app
from minicode_agent.core.state import AgentPhase
from minicode_agent.runtime import RuntimeContext


def test_agent_loop_reaches_done_and_records_trace(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_test")

    result = AgentLoop(runtime, "inspect project").run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.tool_calls == 2
    events = runtime.trace_store.list_events("agent_test")
    event_types = [event.event_type for event in events]
    assert "run_started" in event_types
    assert "phase_changed" in event_types
    assert "tool_requested" in event_types
    assert "run_finished" in event_types


def test_agent_loop_falls_back_to_list_files_without_readme(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_test")

    result = AgentLoop(runtime, "inspect project").run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.tool_calls == 2


def test_cli_run_executes_agent_loop(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["run", "inspect project", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "run_id: agent_" in result.output
    assert "Final phase: done" in result.output
    assert "Tool calls: 2" in result.output


def test_agent_loop_selects_test_writing_skill(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_test")

    result = AgentLoop(runtime, "add unit test for parser").run()

    assert result.state.selected_skills == ["test-writing"]
