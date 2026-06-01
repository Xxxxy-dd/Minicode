from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext
from minicode_agent.trace import TraceEvent, TraceStore, default_trace_db_path
from minicode_agent.trace.store import TRACE_SCHEMA_VERSION


def test_trace_store_appends_and_lists_events(tmp_path) -> None:
    store = TraceStore(tmp_path / "trace.db")

    store.append("run_1", "run_started", {"goal": "test"})
    store.append("run_1", "run_finished", {"ok": True})

    events = store.list_events("run_1")

    assert [event.event_type for event in events] == ["run_started", "run_finished"]
    assert events[0].schema_version == TRACE_SCHEMA_VERSION
    assert events[0].payload["goal"] == "test"


def test_trace_event_defaults_old_records_to_current_schema_version() -> None:
    loaded = TraceEvent.model_validate(
        {
            "id": "event_1",
            "run_id": "run_1",
            "event_type": "tool_requested",
            "timestamp": "2026-05-31T00:00:00+00:00",
            "payload": {"tool": "read_file"},
        }
    )

    assert loaded.schema_version == TRACE_SCHEMA_VERSION


def test_tool_executor_records_trace_events(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
    store = TraceStore(tmp_path / "trace.db")
    executor = ToolExecutor(create_default_registry(), trace_store=store, run_id="run_1")

    observation = executor.execute("read_file", ToolContext(workspace=tmp_path), {"path": "README.md"})

    assert observation.ok
    events = store.list_events("run_1")
    assert [event.event_type for event in events] == [
        "tool_requested",
        "permission_checked",
        "tool_finished",
    ]
    assert events[0].payload["tool"] == "read_file"
    assert events[0].payload["arguments"] == {"path": "README.md"}
    assert events[-1].payload["ok"] is True
    assert "duration_ms" in events[-1].payload


def test_tool_executor_redacts_sensitive_trace_arguments(tmp_path) -> None:
    store = TraceStore(tmp_path / "trace.db")
    executor = ToolExecutor(create_default_registry(), trace_store=store, run_id="run_1")

    executor.execute(
        "write_file",
        ToolContext(workspace=tmp_path),
        {"path": "secret.txt", "content": "hello", "api_key": "should-not-leak"},
    )

    event = store.list_events("run_1")[0]

    assert event.payload["arguments"]["api_key"] == "[redacted]"


def test_tool_executor_redacts_secret_patterns(tmp_path) -> None:
    store = TraceStore(tmp_path / "trace.db")
    executor = ToolExecutor(create_default_registry(), trace_store=store, run_id="run_1")

    executor.execute("run_shell", ToolContext(workspace=tmp_path), {"command": "echo api_key=abc123"})

    event = store.list_events("run_1")[0]

    assert event.payload["arguments"]["command"] == "echo api_key=[redacted]"


def test_trace_store_redacts_payloads_from_all_callers(tmp_path) -> None:
    store = TraceStore(tmp_path / "trace.db")

    store.append(
        "run_1",
        "custom_event",
        {
            "prompt": "Authorization: Bearer abc123",
            "metadata": {"token": "ghp_abcdefghijklmnopqrstuvwxyz"},
            "diff": "+OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz\n",
        },
    )

    event = store.list_events("run_1")[0]
    assert event.payload["prompt"] == "Authorization: Bearer [redacted]"
    assert event.payload["metadata"]["token"] == "[redacted]"
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in event.payload["diff"]


def test_runtime_context_creates_trace_store(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="tool_test")

    assert runtime.workspace == tmp_path.resolve()
    assert runtime.run_id == "tool_test"
    assert runtime.trace_store.storage_path.parent.name == "traces"


def test_cli_tools_run_records_trace(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tools", "run", "read_file", "--workspace", str(tmp_path), "--path", "README.md"],
    )

    assert result.exit_code == 0
    assert "run_id:" in result.output
    assert "trace_backend:" in result.output
    events = TraceStore(default_trace_db_path(tmp_path)).list_events()
    assert [event.event_type for event in events] == [
        "tool_requested",
        "permission_checked",
        "tool_finished",
    ]


def test_cli_trace_lists_events(tmp_path) -> None:
    store = TraceStore(default_trace_db_path(tmp_path))
    store.append("run_1", "tool_requested", {"tool": "read_file"})

    result = CliRunner().invoke(app, ["trace", "run_1", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert "tool_requested" in result.output


def test_cli_trace_json_output(tmp_path) -> None:
    store = TraceStore(default_trace_db_path(tmp_path))
    store.append("run_1", "tool_requested", {"tool": "read_file"})

    result = CliRunner().invoke(app, ["trace", "run_1", "--workspace", str(tmp_path), "--json"])

    assert result.exit_code == 0
    assert f'"schema_version": {TRACE_SCHEMA_VERSION}' in result.output
    assert '"event_type": "tool_requested"' in result.output
