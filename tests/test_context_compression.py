from typer.testing import CliRunner

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.app import app
from minicode_agent.context import TaskStateCompressor
from minicode_agent.core.state import AgentPhase, TaskState
from minicode_agent.models import build_planning_prompt
from minicode_agent.models import ModelMessage, ModelResponse
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolStateEffect


class MockModelClient:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.messages: list[list[ModelMessage]] = []

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        self.messages.append(messages)
        if not self.contents:
            raise AssertionError("Mock model response queue is empty.")
        return ModelResponse(content=self.contents.pop(0), input_tokens=10, output_tokens=5)


def test_task_state_compressor_preserves_structured_fields() -> None:
    compressor = TaskStateCompressor(
        max_summary_chars=300,
        tool_effects={"read_file": {ToolStateEffect.RECORDS_PATH_FACT}},
    )
    state = TaskState(goal="inspect project", constraints=["stay safe"], next_actions=["read docs"])
    observations = [
        {
            "tool": "read_file",
            "ok": True,
            "result": "A" * 1000,
            "metadata": {"path": "README.md"},
            "id": "obs_1",
            "turn": 1,
        },
        {
            "tool": "read_file",
            "ok": False,
            "error": "missing file",
            "metadata": {"path": "missing.md"},
            "id": "obs_2",
            "turn": 2,
        },
    ]

    result = compressor.compress(state, observations)

    assert result.input_chars > result.output_chars
    assert result.task_state.goal == "inspect project"
    assert result.task_state.constraints == ["stay safe"]
    assert "README.md" in result.task_state.files_relevant
    assert result.task_state.known_facts == ["read_file read relevant path: README.md"]
    assert any("missing file" in attempt for attempt in result.task_state.failed_attempts)
    assert result.task_state.history_summary
    assert result.compressed_observation_ids == ["obs_1", "obs_2"]
    assert result.compressed_turns == [1, 2]
    assert result.evidence_refs == [
        {"id": "obs_1", "turn": 1, "tool": "read_file", "ok": True, "path": "README.md"},
        {"id": "obs_2", "turn": 2, "tool": "read_file", "ok": False, "path": "missing.md"},
    ]


def test_task_state_compressor_fallback_preserves_goal() -> None:
    compressor = TaskStateCompressor(max_summary_chars=200)
    state = TaskState(goal="fix tests")

    result = compressor.fallback_compress(state, [{"tool": "read_file", "result": "x" * 1000}], "boom")

    assert result.fallback_used
    assert result.task_state.goal == "fix tests"
    assert "boom" in result.summary


def test_agent_loop_compresses_long_model_observation(tmp_path) -> None:
    (tmp_path / "README.md").write_text("A" * 5000, encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="compression_agent_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Read README.",
              "selected_skill": null,
              "next_actions": ["Read README.md."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
            }
            """,
            """
            {
              "summary": "Enough context gathered.",
              "selected_skill": null,
              "next_actions": ["Report findings."],
              "stop": true,
              "final_answer": "README.md was inspected.",
              "action": null
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "inspect project", model_client=model).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.compression_events == 1
    assert result.state.metrics.compression_input_chars > 0
    assert result.state.metrics.compression_output_chars > 0
    assert result.state.metrics.compression_ratio_avg > 0
    assert result.state.task_state.history_summary
    events = runtime.trace_store.list_events("compression_agent_test")
    compressed = [event for event in events if event.event_type == "context_compressed"]
    assert compressed
    assert compressed[0].payload["ratio"] > 0
    assert compressed[0].payload["compressed_observation_ids"] == ["obs_1"]
    assert compressed[0].payload["compressed_turns"] == [1]
    assert compressed[0].payload["evidence_refs"][0]["path"] == "README.md"
    assert "history_summary" in compressed[0].payload["task_state"]
    assert "context_compressor" in model.messages[1][1].content
    assert '"task_state"' in model.messages[1][1].content
    assert '"history_summary"' in model.messages[1][1].content


def test_agent_loop_compression_fallback(tmp_path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("A" * 5000, encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="compression_fallback_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Read README.",
              "selected_skill": null,
              "next_actions": ["Read README.md."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
            }
            """,
            """
            {
              "summary": "Enough context gathered.",
              "selected_skill": null,
              "next_actions": ["Report findings."],
              "stop": true,
              "final_answer": "README.md was inspected.",
              "action": null
            }
            """,
        ]
    )
    loop = AgentLoop(runtime, "inspect project", model_client=model)

    def fail_compress(*args, **kwargs):
        raise RuntimeError("compress failed")

    monkeypatch.setattr(loop.compressor, "compress", fail_compress)
    result = loop.run()

    assert result.state.current_phase == AgentPhase.DONE
    events = runtime.trace_store.list_events("compression_fallback_test")
    compressed = [event for event in events if event.event_type == "context_compressed"]
    assert compressed[0].payload["fallback_used"] is True


def test_planning_prompt_includes_task_state() -> None:
    state = TaskState(
        goal="fix tests",
        failed_attempts=["missing file"],
        history_summary="Read README and found test instructions.",
    )

    messages = build_planning_prompt("fix tests", ["README.md"], create_default_registry(), task_state=state)

    assert '"task_state"' in messages[1].content
    assert "Read README and found test instructions." in messages[1].content
    assert "missing file" in messages[1].content


def test_cli_trace_shows_compression_ratio(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="trace_compression_test")
    runtime.trace_store.append(
        "trace_compression_test",
        "context_compressed",
        {"ratio": 0.25, "fallback_used": False},
    )

    result = CliRunner().invoke(app, ["trace", "trace_compression_test", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "ratio=0.25" in result.output
    assert "fb=False" in result.output
