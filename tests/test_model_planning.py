import pytest

from minicode_agent.agent import AgentLoop
from minicode_agent.core.state import AgentPhase
from minicode_agent.models import ModelMessage, ModelResponse, build_planning_prompt, parse_model_plan
from minicode_agent.runtime import RuntimeContext
from minicode_agent.tools.registry import create_default_registry


class MockModelClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[ModelMessage] = []

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        self.messages = messages
        return ModelResponse(content=self.content, input_tokens=12, output_tokens=8)


def test_parse_model_plan_requires_json_object() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        parse_model_plan("not json")


def test_parse_model_plan_rejects_missing_action() -> None:
    with pytest.raises(ValueError, match="action"):
        parse_model_plan('{"summary":"x","next_actions":["read"]}')


def test_build_planning_prompt_includes_tools_and_limits_files() -> None:
    messages = build_planning_prompt("inspect", [f"file_{index}.py" for index in range(60)], create_default_registry())

    assert [message.role for message in messages] == ["system", "user"]
    assert "Return only JSON" in messages[0].content
    assert '"available_tools"' in messages[1].content
    assert '"risk_level"' in messages[1].content
    assert '"permission"' in messages[1].content
    assert "file_49.py" in messages[1].content
    assert "file_50.py" not in messages[1].content


def test_agent_loop_uses_mock_model_planner(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_test")
    model = MockModelClient(
        """
        {
          "summary": "Read project documentation.",
          "selected_skill": "debugging",
          "next_actions": ["Read README.md.", "Summarize what changed."],
          "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
        }
        """
    )

    result = AgentLoop(runtime, "fix failing tests", model_client=model).run()

    assert result.state.selected_skills == ["debugging"]
    assert result.state.task_state.next_actions == ["Read README.md.", "Summarize what changed."]
    assert result.state.metrics.input_tokens == 12
    assert result.state.metrics.output_tokens == 8
    assert model.messages[0].role == "system"
    assert "available_tools" in model.messages[1].content
    planned = [event for event in result.transcript if event["event"] == "agent_planned"]
    assert planned[0]["payload"]["planner"] == "model"


def test_agent_loop_records_model_planning_failure(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_failure_test")
    model = MockModelClient('{"summary":"x","next_actions":["try"],"action":{"tool":"missing_tool","arguments":{}}}')

    result = AgentLoop(runtime, "inspect project", model_client=model).run()

    assert result.state.current_phase == AgentPhase.FAILED
    assert "Unknown tool: missing_tool" in result.state.task_state.failed_attempts[0]
    events = runtime.trace_store.list_events("agent_model_failure_test")
    event_types = [event.event_type for event in events]
    assert "model_requested" in event_types
    assert "model_failed" in event_types
    assert "run_finished" in event_types
