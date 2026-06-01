import pytest

from minicode_agent.agent import AgentLoop
from minicode_agent.core.state import AgentPhase
from minicode_agent.models import ModelMessage, ModelResponse, build_planning_prompt, parse_model_plan
from minicode_agent.memory import MemoryKind, MemoryStore
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


def test_parse_model_plan_recovers_from_non_string_final_answer() -> None:
    plan = parse_model_plan(
        """
        {
          "summary": "I can answer directly.",
          "selected_skill": null,
          "next_actions": ["Report the final answer."],
          "stop": true,
          "final_answer": {"text": "I am MiniCode Agent."},
          "action": null
        }
        """
    )

    assert plan.stop
    assert plan.final_answer == "I can answer directly."
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


def test_parse_model_plan_defaults_empty_next_actions_on_stop() -> None:
    plan = parse_model_plan(
        """
        {
          "summary": "你好！",
          "selected_skill": null,
          "next_actions": [],
          "stop": true,
          "final_answer": null,
          "action": null
        }
        """
    )

    assert plan.stop
    assert plan.final_answer == "你好！"
    assert plan.next_actions == ["Report the final answer."]


def test_parse_model_plan_rejects_missing_action() -> None:
    with pytest.raises(ValueError, match="action"):
        parse_model_plan('{"summary":"x","next_actions":["Read README.md."]}')


def test_parse_model_plan_treats_missing_action_with_summary_as_direct_answer() -> None:
    plan = parse_model_plan(
        """
        {
          "summary": "hello",
          "selected_skill": null,
          "next_actions": [],
          "stop": false,
          "final_answer": null,
          "action": null
        }
        """
    )

    assert plan.stop
    assert plan.final_answer == "hello"
    assert plan.action is None


def test_parse_model_plan_allows_report_only_next_action_without_tool() -> None:
    plan = parse_model_plan(
        """
        {
          "summary": "hello",
          "selected_skill": null,
          "next_actions": ["Respond to the user."],
          "stop": false,
          "final_answer": null,
          "action": null
        }
        """
    )

    assert plan.stop
    assert plan.final_answer == "hello"


@pytest.mark.parametrize(
    ("summary", "next_action"),
    [
        ("I am MiniCode Agent, a local coding agent.", "Describe your identity."),
        ("I can inspect, modify, test, review, and document code.", "Describe your capabilities."),
        ("I can help with code tasks and answer project questions.", "Answer what you can help with."),
        ("我是 MiniCode Agent，可以协助代码开发、测试、审查和文档整理。", "介绍你能做什么。"),
    ],
)
def test_parse_model_plan_allows_direct_answer_intents_without_tool(summary: str, next_action: str) -> None:
    plan = parse_model_plan(
        f"""
        {{
          "summary": {summary!r},
          "selected_skill": null,
          "next_actions": [{next_action!r}],
          "stop": false,
          "final_answer": null,
          "action": null
        }}
        """.replace("'", '"')
    )

    assert plan.stop
    assert plan.final_answer == summary
    assert plan.action is None


@pytest.mark.parametrize(
    "next_action",
    [
        "Read README.md.",
        "Read the file src/main.py.",
        "Run tests.",
        "Search code for parser.",
        "Search for parser.",
        "Inspect project files.",
        "Edit file src/main.py.",
        "调用工具读取 README。",
        "搜索项目中的 parser。",
        "检查项目结构。",
        "列出项目文件。",
    ],
)
def test_parse_model_plan_rejects_tool_intent_without_action(next_action: str) -> None:
    with pytest.raises(ValueError, match="action"):
        parse_model_plan(
            f"""
            {{
              "summary": "I need a tool.",
              "selected_skill": null,
              "next_actions": [{next_action!r}],
              "stop": false,
              "final_answer": null,
              "action": null
            }}
            """.replace("'", '"')
        )


def test_parse_model_plan_still_rejects_invalid_action_object() -> None:
    with pytest.raises(ValueError, match="tool"):
        parse_model_plan(
            """
            {
              "summary": "I will use a tool.",
              "selected_skill": null,
              "next_actions": ["Read README."],
              "stop": false,
              "final_answer": null,
              "action": {"arguments": {"path": "README.md"}}
            }
            """
        )


def test_parse_model_plan_requires_final_answer_when_stopping() -> None:
    plan = parse_model_plan('{"summary":"x","next_actions":["report"],"stop":true,"final_answer":null,"action":null}')

    assert plan.final_answer == "x"


def test_agent_loop_recovers_from_bad_direct_answer_payload(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_bad_direct_answer_test")
    model = MockModelClient(
        """
        {
          "summary": "I can answer directly.",
          "selected_skill": null,
          "next_actions": ["Report the final answer."],
          "stop": true,
          "final_answer": {"text": "I am MiniCode Agent."},
          "action": null
        }
        """
    )

    result = AgentLoop(runtime, "你是谁", model_client=model).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert "I can answer directly." in result.state.task_state.decisions


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
    assert "You are MiniCode Agent" in messages[0].content
    assert "tools, skills, memory, trace, subagents, and evaluation harness" in messages[0].content
    assert "First classify the user's request as direct_answer or coding_task" in messages[0].content
    assert "tailor the answer to the exact intent" in messages[0].content
    assert "capability_profile facts" in messages[0].content
    assert "Return only JSON" in messages[0].content
    assert "capability/help questions" in messages[0].content
    assert "permission is ask" in messages[0].content
    assert '"recent_observations"' in messages[1].content
    assert '"user_language": "English"' in messages[1].content
    assert '"response_language": "English"' in messages[1].content
    assert '"active_skills"' in messages[1].content
    assert '"capability_profile"' in messages[1].content
    assert "Debugging" in messages[1].content
    assert '"aliases"' in messages[1].content
    assert "失败" in messages[1].content
    assert '"available_tools"' in messages[1].content
    assert '"direct_answer_policy"' in messages[1].content
    assert '"language_preference"' in messages[1].content
    assert '"tools"' in messages[1].content
    assert '"skills"' in messages[1].content
    assert '"commands"' in messages[1].content
    assert "code-review" in messages[1].content
    assert "/skills" in messages[1].content
    assert '"usage_help"' in messages[1].content
    assert '"conceptual"' in messages[1].content
    assert '"risk_level"' in messages[1].content
    assert '"permission"' in messages[1].content
    assert "file_49.py" in messages[1].content
    assert "file_50.py" not in messages[1].content


def test_build_planning_prompt_declares_chinese_user_language() -> None:
    messages = build_planning_prompt("写一个快速排序到 test.py", ["test.py"], create_default_registry())

    assert '"user_language": "Chinese"' in messages[1].content
    assert "final_answer must also use response_language" in messages[0].content
    assert '"response_language": "Chinese"' in messages[1].content
    assert "Reply in Chinese" in messages[1].content


def test_build_planning_prompt_uses_memory_language_preference(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    memory = store.add(
        MemoryKind.USER,
        "User prefers Chinese replies.",
        confidence=0.9,
        tags=["preference", "language"],
    )[0]

    messages = build_planning_prompt("1+1 = ?", ["README.md"], create_default_registry(), memories=[memory])

    assert '"user_language": "English"' in messages[1].content
    assert '"response_language": "Chinese"' in messages[1].content
    assert "Reply in Chinese" in messages[1].content


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


def test_agent_loop_stops_after_approval_required_tool(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_approval_required_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Try to run a command.",
              "selected_skill": null,
              "next_actions": ["Run a command."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "run_shell", "arguments": {"command": "cmd /c echo hello"}}
            }
            """,
            """
            {
              "summary": "This second response should not be used.",
              "selected_skill": null,
              "next_actions": [],
              "stop": false,
              "final_answer": null,
              "action": null
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "你好", model_client=model).run()

    assert result.state.current_phase == AgentPhase.FAILED
    assert "approval" in result.state.task_state.failed_attempts[-1]
    assert len(model.messages) == 1


def test_agent_loop_runs_approved_write_tool(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_approved_write_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Write a note.",
              "selected_skill": null,
              "next_actions": ["Create notes.txt."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}}
            }
            """,
            """
            {
              "summary": "The file was written.",
              "selected_skill": null,
              "next_actions": [],
              "stop": true,
              "final_answer": "notes.txt written.",
              "action": null
            }
            """,
        ]
    )
    approvals: list[tuple[str, dict, str]] = []

    def approve(tool: str, arguments: dict, reason: str) -> bool:
        approvals.append((tool, arguments, reason))
        return True

    result = AgentLoop(runtime, "write notes.txt", model_client=model, approval_callback=approve).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert approvals == [("write_file", {"path": "notes.txt", "content": "hello"}, "Tool requires approval by its permission mode.")]


def test_agent_loop_blocks_repeated_successful_write_file(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_duplicate_write_test")
    repeated_write = """
    {
      "summary": "Write a note.",
      "selected_skill": null,
      "next_actions": ["Create notes.txt."],
      "stop": false,
      "final_answer": null,
      "action": {"tool": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}}
    }
    """
    model = MockModelClient([repeated_write, repeated_write])
    approval_count = 0

    def approve(tool: str, arguments: dict, reason: str) -> bool:
        nonlocal approval_count
        approval_count += 1
        return True

    result = AgentLoop(runtime, "write notes.txt", model_client=model, approval_callback=approve).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.tool_calls == 2
    assert approval_count == 1
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "hello"
    action_results = [event for event in result.transcript if event["event"] == "action_result"]
    assert [event["payload"]["tool"] for event in action_results] == ["write_file"]
    repeated_blocks = [event for event in result.transcript if event["event"] == "repeated_action_blocked"]
    assert repeated_blocks
    assert repeated_blocks[0]["payload"]["tool"] == "write_file"


def test_agent_loop_can_use_append_file_for_append_intent(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("first\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_append_file_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Append a note.",
              "selected_skill": null,
              "next_actions": ["Append to notes.txt."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "append_file", "arguments": {"path": "notes.txt", "content": "second\\n", "separator": "\\n"}}
            }
            """,
            """
            {
              "summary": "The note was appended.",
              "selected_skill": null,
              "next_actions": [],
              "stop": true,
              "final_answer": "notes.txt appended.",
              "action": null
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "追加一行到 notes.txt", model_client=model, approval_callback=lambda *_: True).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first\nsecond\n"


def test_agent_loop_blocks_write_file_for_append_intent(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("first\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_append_guard_test")
    model = MockModelClient(
        """
        {
          "summary": "Append a note.",
          "selected_skill": null,
          "next_actions": ["Append to notes.txt."],
          "stop": false,
          "final_answer": null,
          "action": {"tool": "write_file", "arguments": {"path": "notes.txt", "content": "second\\n"}}
        }
        """
    )

    result = AgentLoop(runtime, "追加一行到 notes.txt", model_client=model, approval_callback=lambda *_: True).run()

    assert result.state.current_phase == AgentPhase.FAILED
    assert "append intent should use append_file" in result.state.task_state.failed_attempts[-1]
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first\n"


def test_agent_loop_allows_edit_file_for_replace_intent(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("old\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_replace_guard_test")
    model = MockModelClient(
        [
            """
            {
              "summary": "Replace text.",
              "selected_skill": null,
              "next_actions": ["Replace old with new."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "edit_file", "arguments": {"path": "notes.txt", "old_text": "old", "new_text": "new"}}
            }
            """,
            """
            {
              "summary": "The text was replaced.",
              "selected_skill": null,
              "next_actions": [],
              "stop": true,
              "final_answer": "notes.txt edited.",
              "action": null
            }
            """,
        ]
    )

    result = AgentLoop(runtime, "replace old in notes.txt", model_client=model, approval_callback=lambda *_: True).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "new\n"


def test_agent_loop_respects_rejected_write_tool(tmp_path) -> None:
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_rejected_write_test")
    model = MockModelClient(
        """
        {
          "summary": "Write a note.",
          "selected_skill": null,
          "next_actions": ["Create notes.txt."],
          "stop": false,
          "final_answer": null,
          "action": {"tool": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}}
        }
        """
    )

    result = AgentLoop(runtime, "write notes.txt", model_client=model, approval_callback=lambda *_: False).run()

    assert result.state.current_phase == AgentPhase.FAILED
    assert not (tmp_path / "notes.txt").exists()
    assert "approval" in result.state.task_state.failed_attempts[-1]


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


def test_agent_loop_blocks_repeated_successful_read_file(tmp_path) -> None:
    readme = "# Demo\n\n" + "content line\n" * 500
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_duplicate_tool_test")
    repeated_read = """
    {
      "summary": "Read README again.",
      "selected_skill": null,
      "next_actions": ["Read README.md."],
      "stop": false,
      "final_answer": null,
      "action": {"tool": "read_file", "arguments": {"path": "README.md"}}
    }
    """
    model = MockModelClient([repeated_read, repeated_read])

    result = AgentLoop(runtime, "读取 README.md", model_client=model).run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.tool_calls == 2
    assert len(model.messages) == 2
    action_results = [event for event in result.transcript if event["event"] == "action_result"]
    assert [event["payload"]["tool"] for event in action_results] == ["read_file"]
    assert action_results[0]["payload"]["truncated"]
    assert len(action_results[0]["payload"]["output"]) < len(readme)
    repeated_blocks = [event for event in result.transcript if event["event"] == "repeated_action_blocked"]
    assert repeated_blocks
    assert repeated_blocks[0]["payload"]["tool"] == "read_file"
    assert result.state.task_state.decisions[0] == readme.strip()
    assert "阻止重复执行" not in result.state.task_state.decisions[0]


def test_agent_loop_model_stops_at_max_steps(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# Notes\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_model_max_steps_test")
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
              "summary": "Read notes.",
              "selected_skill": null,
              "next_actions": ["Read notes.md."],
              "stop": false,
              "final_answer": null,
              "action": {"tool": "read_file", "arguments": {"path": "notes.md"}}
            }
            """,
        ]
    )

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
