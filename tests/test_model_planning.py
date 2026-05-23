import pytest

from minicode_agent.agent import AgentLoop
from minicode_agent.core.state import AgentPhase
from minicode_agent.models import ModelMessage, ModelResponse, build_planning_prompt, parse_model_plan
from minicode_agent.runtime import RuntimeContext
from minicode_agent.skills import SkillRegistry
from minicode_agent.tools.registry import create_default_registry


class MockModelClient:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = [content] if isinstance(content, str) else list(content)
        self.messages: list[list[ModelMessage]] = []

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        self.messages.append(messages)
        if not self.contents:
            raise AssertionError("Mock model response queue is empty.")
        content = self.contents.pop(0)
        return ModelResponse(content=content, input_tokens=12, output_tokens=8)


def test_parse_model_plan_requires_json_object() -> None:
    plan = parse_model_plan("not json")

    assert plan.stop
    assert plan.final_answer == "not json"
    assert plan.action is None


def test_parse_model_plan_extracts_markdown_json() -> None:
    plan = parse_model_plan(
        """
        ```json
        {
          "summary": "Done.",
          "selected_skill": null,
          "next_actions": ["Report result."],
          "stop": true,
          "final_answer": "All set.",
          "action": null
        }
        ```
        """
    )

    assert plan.stop
    assert plan.final_answer == "All set."


def test_parse_model_plan_extracts_json_from_explained_response() -> None:
    plan = parse_model_plan(
        """
        I will read the README first.
        {
          "summary": "Read README.",
          "selected_skill": null,
          "next_actions": ["Read README.md."],
          "stop": false,
          "final_answer": null,
          "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
        }
        """
    )

    assert plan.action is not None
    assert plan.action.tool == "read_file"


def test_parse_model_plan_rejects_missing_action() -> None:
    with pytest.raises(ValueError, match="action"):
        parse_model_plan('{"summary":"x","next_actions":["read"]}')


def test_parse_model_plan_requires_final_answer_when_stopping() -> None:
    with pytest.raises(ValueError, match="final_answer"):
        parse_model_plan('{"summary":"x","next_actions":["report"],"stop":true,"final_answer":null,"action":null}')


def test_build_planning_prompt_includes_tools_and_limits_files() -> None:
    debugging = SkillRegistry().get("debugging")
    messages = build_planning_prompt(
        "inspect",
        [f"file_{index}.py" for index in range(60)],
        create_default_registry(),
        observations=[{"tool": "read_file", "ok": True, "result": "done"}],
        skills=[debugging],
    )

    assert [message.role for message in messages] == ["system", "user"]
    assert "Return only JSON" in messages[0].content
    assert '"recent_observations"' in messages[1].content
    assert '"active_skills"' in messages[1].content
    assert "Debugging" in messages[1].content
    assert '"aliases"' in messages[1].content
    assert "失败" in messages[1].content
    assert '"available_tools"' in messages[1].content
    assert '"risk_level"' in messages[1].content
    assert '"permission"' in messages[1].content
    assert "file_49.py" in messages[1].content
    assert "file_50.py" not in messages[1].content


def test_agent_loop_uses_mock_model_planner(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Read project documentation.",
              "selected_skill": "debugging",
              "next_actions": ["Read README.md.", "Summarize what changed."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
            }
            """,
            """
            {
              "summary": "The README was inspected.",
              "selected_skill": "debugging",
              "next_actions": ["Report the result."],
              "stop": true,
              "final_answer": "README.md was read.",
              "action": null
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "fix failing tests", model_client=model).run()

    assert result.state.selected_skills == ["debugging"]
    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.task_state.next_actions == ["Report the result."]
    assert result.state.metrics.input_tokens == 24
    assert result.state.metrics.output_tokens == 16
    assert model.messages[0][0].role == "system"
    assert "available_tools" in model.messages[0][1].content
    assert "Debugging" in model.messages[0][1].content
    assert "README.md was read." in result.state.task_state.decisions
    planned = [event for event in result.transcript if event["event"] == "agent_planned"]
    assert planned[0]["payload"]["planner"] == "model"
    assert planned[-1]["payload"]["stop"] is True
    events = runtime.trace_store.list_events("agent_model_test")
    first_model_request = next(event for event in events if event.event_type == "model_requested")
    assert first_model_request.payload["skill_count"] >= 1


def test_agent_loop_treats_direct_model_answer_as_stop(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_direct_answer_test")
    model = MockModelClient("我是 MiniCode Agent。")

    result = AgentLoop(runtime, "你是什么模型", model_client=model).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert "我是 MiniCode Agent。" in result.state.task_state.decisions


def test_agent_loop_records_model_planning_failure(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_failure_test")
    model = MockModelClient(
        '{"summary":"x","next_actions":["try"],"stop":false,"final_answer":null,"action":{"tool":"missing_tool","arguments":{}}}'
    )

    result = AgentLoop(runtime, "inspect project", model_client=model).run()

    assert result.state.current_phase == AgentPhase.FAILED
    assert "Unknown tool: missing_tool" in result.state.task_state.failed_attempts[0]
    events = runtime.trace_store.list_events("agent_model_failure_test")
    event_types = [event.event_type for event in events]
    assert "model_requested" in event_types
    assert "model_failed" in event_types
    assert "run_finished" in event_types


def test_agent_loop_model_can_call_multiple_readonly_tools(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\nneedle\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_multi_tool_test")
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
              "summary": "Search for a term from README.",
              "selected_skill": null,
              "next_actions": ["Search for needle."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "search_code", "arguments": {"pattern": "needle"}}
            }
            """,
            """
            {
              "summary": "Enough context gathered.",
              "selected_skill": null,
              "next_actions": ["Report findings."],
              "stop": true,
              "final_answer": "Read README.md and found needle.",
              "action": null
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "read and summarize project", model_client=model).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.tool_calls == 3
    assert len(model.messages) == 3
    assert '"recent_observations"' in model.messages[1][1].content
    assert "needle" in model.messages[1][1].content
    assert "README.md:2: needle" in model.messages[2][1].content
    action_results = [event["payload"]["tool"] for event in result.transcript if event["event"] == "action_result"]
    assert action_results == ["read_file", "search_code"]
    first_action = next(event["payload"] for event in result.transcript if event["event"] == "action_result")
    assert first_action["output"] == "# Demo\nneedle\n"
    events = runtime.trace_store.list_events("agent_model_multi_tool_test")
    model_events = [event for event in events if event.event_type == "model_requested"]
    assert model_events[0].payload["turn"] == 1
    assert model_events[1].payload["observation_count"] == 1


def test_agent_loop_model_stops_at_max_steps(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_max_steps_test")
    keep_reading = """
    {
      "summary": "Keep reading README.",
      "selected_skill": null,
      "next_actions": ["Read README.md."],
      "stop": false,
      "final_answer": null,
      "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
    }
    """
    model = MockModelClient([keep_reading, keep_reading])

    result = AgentLoop(runtime, "loop forever", max_steps=2, model_client=model).run()

    assert result.state.current_phase == AgentPhase.FAILED
    assert "max agent steps exceeded" in result.state.task_state.failed_attempts
    assert len(model.messages) == 2


def test_agent_loop_model_replans_after_tool_failure(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_replan_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Try a missing file.",
              "selected_skill": null,
              "next_actions": ["Read missing file."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "missing.md"}}
            }
            """,
            """
            {
              "summary": "Recover by reading README.",
              "selected_skill": null,
              "next_actions": ["Read README.md."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
            }
            """,
            """
            {
              "summary": "Recovered.",
              "selected_skill": null,
              "next_actions": ["Report result."],
              "stop": true,
              "final_answer": "Recovered after missing file.",
              "action": null
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "recover from failure", model_client=model).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.retries == 1
    assert any("does not exist" in attempt.lower() for attempt in result.state.task_state.failed_attempts)


def test_agent_loop_model_respects_failed_tool_attempt_limit(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_retry_limit_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Try a missing file.",
              "selected_skill": null,
              "next_actions": ["Read missing file."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "missing.md"}}
            }
            """,
            """
            {
              "summary": "Try missing again.",
              "selected_skill": null,
              "next_actions": ["Read missing file again."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "missing.md"}}
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "fail fast", max_failed_tool_attempts=1, model_client=model).run()

    assert result.state.current_phase == AgentPhase.FAILED
    assert result.state.metrics.retries == 2
    assert result.state.task_state.failed_attempts[-1] == "tool failed too many times"
