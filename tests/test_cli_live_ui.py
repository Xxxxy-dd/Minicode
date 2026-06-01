from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.cli.chat_commands import CHAT_COMMANDS, handle_chat_command, help_notice
from minicode_agent.cli.live_ui import (
    ChatRunSnapshot,
    ChatSession,
    ChatTurn,
    build_direct_chat_turn,
    build_approval_callback,
    format_stream_event,
    input_bar,
    language_preference_from_text,
    load_preferred_language,
    load_user_preferences,
    localize_summary_language,
    persist_user_preference,
    render_compact_approval_preview,
    render_bottom_panel,
    render_conversation_area,
    render_chat_intro,
    render_latest_turn,
    refresh_recent_activity,
    refresh_chat_intro,
    recent_activity_column,
    recent_activity_lines,
    recent_activity_position,
    render_top_panel,
    run_turn,
    seed_recent_chat_context,
    summarize_turn,
    wrap_text,
)
from minicode_agent.memory import MemoryKind, MemoryStore
from minicode_agent.models import ModelResponse
from minicode_agent.cli.preview_renderer import render_preview_text
from minicode_agent.config import MiniCodeConfig
from minicode_agent.core.state import AgentPhase, AgentState, TaskState


class DirectChatModel:
    def __init__(self, content: str | list[str] = "模型动态回答。") -> None:
        self.contents = [content] if isinstance(content, str) else list(content)
        self.messages = []

    def complete(self, messages):
        self.messages.append(messages)
        if not self.contents:
            raise AssertionError("DirectChatModel response queue is empty.")
        return ModelResponse(content=self.contents.pop(0), input_tokens=10, output_tokens=5)


def test_chat_command_is_exposed() -> None:
    result = CliRunner().invoke(app, ["chat", "--help"])

    assert result.exit_code == 0, result.output
    assert "Open the Claude-like interactive CLI layout." in result.output
    assert "Optional initial task" in result.output
    assert "--preview" in result.output


def test_claude_like_panels_render_key_sections() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(
                prompt="inspect project",
                run_id="run_1",
                final_phase="done",
                tool_calls=2,
                selected_skills=["code-review"],
                summary="Inspected README and project layout.",
            )
        ],
    )

    console = Console(record=True, width=120)
    console.print(render_top_panel(session))
    console.print(render_conversation_area(session))
    console.print(render_bottom_panel(session))
    text = console.export_text()

    assert "MiniCode Agent" in text
    assert "Recent Activity" in text
    assert "no-model" in text
    assert "Command" in text
    assert "USER" in text
    assert "MINICODE" in text
    assert "inspect project" in text
    assert "Inspected README and project layout." in text


def test_chat_preview_renders_a_full_screen() -> None:
    result = CliRunner().invoke(app, ["chat", "--workspace", ".", "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "MiniCode Agent" in result.output
    assert "Command" in result.output
    assert "No recent activity" in result.output
    assert "No messages yet" in result.output


def test_latest_turn_render_does_not_repeat_top_card() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(
                prompt="inspect project",
                run_id="run_1",
                final_phase="done",
                tool_calls=2,
                selected_skills=[],
                summary="Inspected README.",
            )
        ],
    )

    console = Console(record=True, width=120)
    render_latest_turn(session, console)
    text = console.export_text()

    assert "USER" in text
    assert "MINICODE" in text
    assert "inspect project" in text
    assert "Inspected README." in text
    assert "MiniCode Agent" not in text
    assert "Recent Activity" not in text
    assert "phase:" not in text
    assert "tool:" not in text
    assert "skills:" not in text


def test_chat_intro_does_not_render_fixed_command_panel() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )

    console = Console(record=True, width=120)
    render_chat_intro(session, console)
    text = console.export_text()

    assert "MiniCode Agent" in text
    assert "no-model" in text
    assert "Enter a task" not in text
    assert "/help" not in text


def test_refresh_chat_intro_is_noop_for_recorded_console() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(
                prompt="hello",
                run_id="run_1",
                final_phase="done",
                tool_calls=0,
                summary="hi",
            )
        ],
    )
    console = Console(record=True, width=120)

    refresh_chat_intro(session, console)

    assert console.export_text() == ""


def test_refresh_recent_activity_is_noop_for_recorded_console() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(
                prompt="hello",
                run_id="run_1",
                final_phase="done",
                tool_calls=0,
                summary="hi",
            )
        ],
    )
    console = Console(record=True, width=120)

    refresh_recent_activity(session, console)

    assert console.export_text() == ""


def test_recent_activity_lines_show_latest_history_only() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(prompt=f"task {index}", run_id=f"run_{index}", final_phase="done", tool_calls=index, summary="ok")
            for index in range(6)
        ],
    )

    lines = recent_activity_lines(session)
    plain = "\n".join(line.plain for line in lines)

    assert "Recent Activity" in plain
    assert "task 0" not in plain
    assert "task 1" not in plain
    assert "task 2" in plain
    assert "task 5" in plain


def test_recent_activity_column_uses_rendered_top_panel_position() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )
    console = Console(record=True, width=120)

    column = recent_activity_column(session, console)
    rendered_lines = console.render_lines(render_top_panel(session), console.options)
    expected = next(
        "".join(segment.text for segment in line).find("Recent Activity") + 1
        for line in rendered_lines
        if "Recent Activity" in "".join(segment.text for segment in line)
    )

    assert column == expected
    assert column > 3


def test_recent_activity_position_uses_rendered_title_row() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(prompt="你好", run_id="chat_direct", final_phase="done", tool_calls=0, summary="ok"),
            ChatTurn(prompt="你是什么模型", run_id="chat_direct", final_phase="done", tool_calls=0, summary="ok"),
        ],
    )
    console = Console(record=True, width=120)

    row, column = recent_activity_position(session, console)
    rendered_lines = console.render_lines(render_top_panel(session), console.options)
    matches = [
        (index, "".join(segment.text for segment in line).find("Recent Activity") + 1)
        for index, line in enumerate(rendered_lines, start=1)
        if "Recent Activity" in "".join(segment.text for segment in line)
    ]

    assert matches == [(row, column)]


def test_help_command_shows_a_system_notice() -> None:
    result = CliRunner().invoke(app, ["chat", "/help", "--workspace", ".", "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "SYSTEM" in result.output
    assert "Shortcuts:" in result.output
    assert "/status" in result.output
    assert "/tools" in result.output
    assert "/config" in result.output
    assert "/last" in result.output


def test_help_notice_is_generated_from_registered_chat_commands() -> None:
    text = help_notice()

    for command in CHAT_COMMANDS:
        assert command in text
    assert "/clear" in text
    assert "/exit" in text


def test_interactive_input_bar_has_focus_rules() -> None:
    result = CliRunner().invoke(app, ["chat", "--workspace", ".", "--no-model"], input="/exit\n")

    assert result.exit_code == 0, result.output
    assert "Command" in result.output
    assert "? for shortcuts" not in result.output
    assert "UnboundLocalError" not in result.output


def test_interactive_chat_renders_top_card_once_and_appends_history(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["chat", "--workspace", str(tmp_path), "--no-model"],
        input="你有什么工具\n你有什么skills\n/exit\n",
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("MiniCode Agent") == 1
    assert result.output.count("Recent Activity") == 1
    assert "你有什么工具" in result.output
    assert "你有什么skills" in result.output
    assert "\x1b[4;" not in result.output


def test_recent_activity_is_not_repainted_into_history(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["chat", "--workspace", str(tmp_path), "--no-model"],
        input="你好\n你是什么模型\n/exit\n",
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("Recent Activity") == 1


def test_interactive_chat_refreshes_recent_activity_after_turn(monkeypatch, tmp_path) -> None:
    refresh_counts = []
    monkeypatch.setattr("minicode_agent.cli.live_ui.refresh_recent_activity", lambda session: refresh_counts.append(len(session.turns)))

    result = CliRunner().invoke(
        app,
        ["chat", "--workspace", str(tmp_path), "--no-model"],
        input="你好\n你是什么模型\n/exit\n",
    )

    assert result.exit_code == 0, result.output
    assert refresh_counts == [1, 2]
    assert result.output.count("Recent Activity") == 1


def test_input_bar_does_not_render_extra_rules(monkeypatch) -> None:
    console = Console(record=True, width=40)
    monkeypatch.setattr("minicode_agent.cli.live_ui.Console", lambda: console)
    monkeypatch.setattr(console, "input", lambda prompt: (console.print(prompt), "hello")[1])

    value, panel_height = input_bar()
    assert value == "hello"
    assert panel_height > 0
    assert panel_height == len(console.render_lines(render_bottom_panel(None), console.options)) + 1
    text = console.export_text()
    assert "Command" in text
    assert "Enter a task" in text
    assert "> " in text


def test_format_stream_event_hides_phase_and_shows_tool() -> None:
    phase = format_stream_event("phase_changed", {"phase": "plan", "reason": "Draft a short plan."})
    tool = format_stream_event("action_result", {"tool": "read_file", "ok": True, "result": "README.md"})

    assert phase is None
    assert tool is not None
    assert "read_file" in tool.plain


def test_approval_prompt_only_asks_for_yes_or_no(monkeypatch) -> None:
    console = Console(record=True, width=120)
    monkeypatch.setattr(console, "input", lambda prompt: (console.print(prompt), "y")[1])

    approved = build_approval_callback(console)(
        "write_file",
        {"path": "secret.txt", "content": "hidden content"},
        "Tool requires approval by its permission mode.",
    )

    text = console.export_text()
    assert approved is True
    assert "是否批准？[y/N]" in text
    assert "write_file" not in text
    assert "secret.txt" not in text
    assert "hidden content" not in text


def test_approval_prompt_pauses_status_before_input(monkeypatch) -> None:
    console = Console(record=True, width=120)

    class FakeStatus:
        def __init__(self) -> None:
            self.stopped = False
            self.started = False

        def stop(self) -> None:
            self.stopped = True

        def start(self) -> None:
            self.started = True

    status = FakeStatus()

    def input_with_status_check(prompt):
        assert status.stopped is True
        console.print(prompt)
        return "n"

    monkeypatch.setattr(console, "input", input_with_status_check)
    approved = build_approval_callback(console, status)(
        "append_file",
        {"path": "test.py", "content": "print('hi')"},
        "Tool requires approval by its permission mode.",
        {
            "summary": "Create test.py: +1 lines.",
            "operation": "append",
            "paths": ["test.py"],
            "stats": {"insertions": 1, "deletions": 0, "hunks": 1},
            "diff": "--- a/test.py\n+++ b/test.py\n@@ -0,0 +1 @@\n+print('hi')\n",
            "display_blocks": [
                {
                    "kind": "append",
                    "title": "Append preview",
                    "content": "--- a/test.py\n+++ b/test.py\n@@ -0,0 +1 @@\n+print('hi')\n",
                }
            ],
        },
    )

    text = console.export_text()
    assert approved is False
    assert status.started is True
    assert "Pending write: append_file" in text
    assert "operation=append" in text
    assert "是否批准？[y/N]" in text
    assert "未应用变更" in text


def test_approval_prompt_records_preview_before_input(monkeypatch) -> None:
    console = Console(record=True, width=120)
    seen = []
    preview = {
        "summary": "Create test.py: +1 lines.",
        "operation": "append",
        "paths": ["test.py"],
        "stats": {"insertions": 1, "deletions": 0, "hunks": 1},
        "display_blocks": [
            {
                "kind": "append",
                "title": "Append preview",
                "content": "+print('hi')",
            }
        ],
    }
    monkeypatch.setattr(console, "input", lambda prompt: (console.print(prompt), "n")[1])

    build_approval_callback(console, on_preview=seen.append)(
        "append_file",
        {"path": "test.py", "content": "print('hi')"},
        "Tool requires approval by its permission mode.",
        preview,
    )

    assert seen == [preview]


def test_approval_prompt_can_clear_preview_after_decision(monkeypatch) -> None:
    console = Console(record=True, width=120, force_terminal=True)
    preview = {
        "summary": "Append to app.py: +1 lines.",
        "operation": "append",
        "paths": ["app.py"],
        "stats": {"insertions": 1, "deletions": 0, "hunks": 1},
        "display_blocks": [
            {
                "kind": "append",
                "title": "Append preview",
                "content": "+print('hi')",
            }
        ],
    }
    monkeypatch.setattr(console, "input", lambda prompt: (console.print(prompt), "y")[1])

    approved = build_approval_callback(console, clear_after=True)(
        "append_file",
        {"path": "app.py", "content": "print('hi')"},
        "Tool requires approval by its permission mode.",
        preview,
    )

    assert approved is True
    text = console.export_text()
    assert "Full preview is available with /diff" in text
    assert "+print('hi')" not in text


def test_compact_approval_preview_omits_code_body() -> None:
    text = render_compact_approval_preview(
        "append_file",
        {
            "summary": "Append to app.py: +1 lines.",
            "operation": "append",
            "paths": ["app.py"],
            "stats": {"insertions": 1, "deletions": 0, "hunks": 1},
            "display_blocks": [{"content": "+print('hi')"}],
        },
    )

    assert "Append to app.py" in text
    assert "app.py" in text
    assert "+print('hi')" not in text


def test_preview_renderer_prefers_structured_display_blocks() -> None:
    preview = {
        "summary": "Append to app.py: +2 lines.",
        "operation": "append",
        "paths": ["app.py"],
        "stats": {"insertions": 2, "deletions": 0, "hunks": 1},
        "diff": "legacy diff",
        "display_blocks": [
            {
                "kind": "append",
                "title": "Append preview",
                "content": "@@ append after end of file @@\n def one():\n+def two():",
            }
        ],
        "risk_notes": [],
    }

    text = render_preview_text(preview, heading="Pending write: append_file")

    assert "Pending write: append_file" in text
    assert "operation=append" in text
    assert "[Append preview]" in text
    assert "+def two():" in text
    assert "legacy diff" not in text


def test_summarize_turn_prefers_direct_final_answer() -> None:
    class Result:
        transcript = [
            {
                "event": "agent_planned",
                "payload": {"stop": True, "description": "Model returned a direct answer."},
            }
        ]
        state = AgentState(
            run_id="run_1",
            workspace=".",
            user_goal="hello",
            current_phase=AgentPhase.DONE,
            task_state=TaskState(
                goal="hello",
                decisions=["你好，我是 MiniCode。", "Keep the first loop minimal and traceable."],
            ),
        )

    assert summarize_turn(Result()) == "你好，我是 MiniCode。"


def test_summarize_turn_preserves_full_decision_text() -> None:
    long_answer = "# Demo\n\n" + "content line\n" * 80

    class Result:
        transcript = []
        state = AgentState(
            run_id="run_1",
            workspace=".",
            user_goal="read README",
            current_phase=AgentPhase.DONE,
            task_state=TaskState(goal="read README", decisions=[long_answer]),
        )

    assert summarize_turn(Result()) == long_answer


def test_localize_summary_language_hides_english_for_chinese_task() -> None:
    assert localize_summary_language("写一个快速排序", "The quicksort algorithm has been written.") == (
        "模型返回的最终说明语言与当前请求不一致；变更已完成，请用 /trace 或 /diff 查看本轮细节。"
    )
    assert localize_summary_language("write quicksort", "The quicksort algorithm has been written.") == "The quicksort algorithm has been written."


def test_localize_summary_language_uses_session_preference_for_symbolic_task(tmp_path) -> None:
    session = ChatSession(
        workspace=tmp_path,
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        preferred_language="zh",
        user_preferences=["User prefers Chinese replies."],
    )
    model = DirectChatModel("答案是 2。")

    summary = localize_summary_language("1+1 = ?", "The answer is 2.", session=session, model_client=model)

    assert summary == "答案是 2。"
    assert len(model.messages) == 1
    assert '"response_language": "Chinese"' in model.messages[0][1].content


def test_direct_chat_query_is_answered_without_agent_loop(tmp_path) -> None:
    result = CliRunner().invoke(app, ["chat", "你有什么工具", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert '"fallback": "no_model"' in result.output
    assert '"capability_profile"' in result.output
    assert "read_file" in result.output
    assert "write_file" in result.output
    assert "phase init" not in result.output


def test_direct_chat_uses_shared_capability_patterns(tmp_path) -> None:
    result = CliRunner().invoke(app, ["chat", "what tools do you have", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "run_shell" in result.output
    assert '"fallback": "no_model"' in result.output
    assert "phase init" not in result.output


def test_direct_chat_answers_skills_introspection_in_user_language(tmp_path) -> None:
    result = CliRunner().invoke(app, ["chat", "你有什么skills", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert '"fallback": "no_model"' in result.output
    assert '"skills"' in result.output
    assert "code-review" in result.output
    assert "debugging" in result.output


def test_direct_chat_answers_commands_introspection(tmp_path) -> None:
    result = CliRunner().invoke(app, ["chat", "有哪些命令", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert '"fallback": "no_model"' in result.output
    assert "/skills" in result.output
    assert "/tools" in result.output


def test_direct_chat_model_question_reports_current_model(tmp_path) -> None:
    session = ChatSession(
        workspace=tmp_path,
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )
    model = DirectChatModel("我是模型根据运行上下文回答的。")

    turn = build_direct_chat_turn(session, "你是什么模型", model_client=model)

    assert turn.summary == "我是模型根据运行上下文回答的。"
    assert turn.trace_backend == "model"
    assert model.messages
    assert "demo-model" in model.messages[0][1].content
    assert "capability_profile" in model.messages[0][1].content


def test_direct_chat_model_turn_runs_under_thinking_status(monkeypatch, tmp_path) -> None:
    session = ChatSession(
        workspace=tmp_path,
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )
    model = DirectChatModel("模型回答。")
    console = Console(record=True, width=120)
    status_events = []
    clear_events = []

    class FakeStatus:
        def __enter__(self):
            status_events.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            status_events.append("exit")
            return False

    def fake_status(message, spinner=None):
        status_events.append((message.plain if hasattr(message, "plain") else str(message), spinner))
        return FakeStatus()

    monkeypatch.setattr("minicode_agent.cli.live_ui.Console", lambda: console)
    monkeypatch.setattr(console, "status", fake_status)
    monkeypatch.setattr("minicode_agent.cli.live_ui.clear_previous_terminal_lines", lambda _console, count: clear_events.append(count))

    turn = run_turn(session, "你是什么模型", MiniCodeConfig.from_env(tmp_path), model, input_panel_height=2)

    assert turn.summary == "模型回答。"
    assert clear_events == [2]
    assert status_events[0][1] == "dots"
    assert "MiniCode is thinking" in status_events[0][0]
    assert status_events[1:] == ["enter", "exit"]


def test_direct_chat_with_model_receives_dynamic_capability_profile(tmp_path) -> None:
    session = ChatSession(
        workspace=tmp_path,
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        preferred_language="zh",
        user_preferences=["User prefers Chinese replies."],
        turns=[ChatTurn(prompt="上一轮", run_id="chat_direct", final_phase="done", tool_calls=0, summary="上一轮回答")],
    )
    model = DirectChatModel("这是模型结合能力包后的回答。")

    turn = build_direct_chat_turn(session, "你有什么skills", model_client=model)

    assert turn.summary == "这是模型结合能力包后的回答。"
    assert model.messages
    prompt = model.messages[0][1].content
    assert '"capability_profile"' in prompt
    assert "code-review" in prompt
    assert "read_file" in prompt
    assert "/skills" in prompt
    assert "User prefers Chinese replies." in prompt
    assert '"response_language": "Chinese"' in prompt
    assert "上一轮" in prompt


def test_direct_chat_rewrites_model_answer_to_preferred_language(tmp_path) -> None:
    session = ChatSession(
        workspace=tmp_path,
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        preferred_language="zh",
        user_preferences=["User prefers Chinese replies."],
    )
    model = DirectChatModel(["The answer is 2.", "答案是 2。"])

    turn = build_direct_chat_turn(session, "1+1 = ?", model_client=model)

    assert turn.summary == "答案是 2。"
    assert len(model.messages) == 2
    assert '"response_language": "Chinese"' in model.messages[0][1].content
    assert '"assistant_response": "The answer is 2."' in model.messages[1][1].content


def test_direct_chat_blocks_english_when_rewrite_ignores_preference(tmp_path) -> None:
    session = ChatSession(
        workspace=tmp_path,
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        preferred_language="zh",
        user_preferences=["User prefers Chinese replies."],
    )
    model = DirectChatModel(["The answer is 2.", "Still English."])

    turn = build_direct_chat_turn(session, "1+1 = ?", model_client=model)

    assert "模型返回的回答语言与当前偏好不一致" in turn.summary
    assert not turn.summary.startswith("Still English")


def test_direct_chat_remembers_previous_user_message() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(prompt="你好", run_id="chat_direct", final_phase="done", tool_calls=0, summary="你好"),
        ],
        preferred_language="zh",
    )

    model = DirectChatModel("你上一句的内容已在上下文中。")

    turn = build_direct_chat_turn(session, "我上一句说了什么", model_client=model)

    assert turn.summary == "你上一句的内容已在上下文中。"
    assert "你好" in model.messages[0][1].content


def test_language_preference_is_persisted_as_user_memory(tmp_path) -> None:
    assert language_preference_from_text("以后请一直说中文") == "zh"

    persist_user_preference(tmp_path, "User prefers Chinese replies.", tags=["preference", "language"])

    assert load_preferred_language(tmp_path) == "zh"
    assert "User prefers Chinese replies." in load_user_preferences(tmp_path)


def test_recent_chat_context_is_seeded_as_session_memory(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    session = ChatSession(
        workspace=tmp_path,
        model_name="demo-model",
        model_base_url="https://api.openai.com/v1",
        no_model=False,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(prompt="先检查 README", run_id="run_1", final_phase="done", tool_calls=1, summary="README 已检查。"),
            ChatTurn(prompt="然后记住我的偏好", run_id="run_2", final_phase="done", tool_calls=0, summary="偏好已记录。"),
        ],
    )

    seed_recent_chat_context(store, session)

    records = store.search("", kind=MemoryKind.USER, tags=["session", "conversation"])
    assert records
    assert "先检查 README" in records[0].content
    assert "偏好已记录" in records[0].content


def test_chat_task_still_runs_agent_loop_for_workspace_work(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["chat", "inspect project", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "phase init" not in result.output
    assert "MiniCode Agent" in result.output
    assert "phase:" not in result.output
    assert "tool:" not in result.output


def test_chat_status_command_reports_latest_turn() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(
                prompt="inspect project",
                run_id="run_1",
                final_phase="done",
                tool_calls=2,
                selected_skills=["repo-onboarding"],
                summary="Inspected README.",
                phase="done",
                last_tool="read_file",
                last_tool_ok=True,
                last_tool_result="README.md",
            )
        ],
        snapshot=ChatRunSnapshot(
            phase="done",
            tool="read_file",
            tool_ok=True,
            tool_result="README.md",
            trace_run_id="run_1",
        ),
    )

    done = handle_chat_command("/status", session)

    assert done is False
    assert "phase: done" in session.notices[-1]
    assert "read_file ok" in session.notices[-1]
    assert "trace_id: run_1" in session.notices[-1]


def test_chat_diff_command_reports_missing_preview() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )

    done = handle_chat_command("/diff", session)

    assert done is False
    assert "(no diff preview recorded)" in session.notices[-1]


def test_chat_trace_command_reports_missing_run() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )

    done = handle_chat_command("/trace", session)

    assert done is False
    assert "(no run trace yet)" in session.notices[-1]


def test_chat_skills_command_reports_missing_task() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )

    done = handle_chat_command("/skills", session)

    assert done is False
    assert "(no task to route yet)" in session.notices[-1]


def test_chat_tools_command_reports_registry() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )

    done = handle_chat_command("/tools", session)

    assert done is False
    assert "tools:" in session.notices[-1]
    assert "read_file risk=safe permission=allow" in session.notices[-1]
    assert "write_file risk=medium permission=ask" in session.notices[-1]


def test_chat_config_command_reports_session_settings() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name="demo-model",
        model_base_url="https://example.test/v1",
        no_model=False,
        llm_rerank=True,
        memory_reflection_mode="llm",
        interactive_approval=False,
    )

    done = handle_chat_command("/config", session)

    assert done is False
    assert "model: demo-model" in session.notices[-1]
    assert "llm_rerank: True" in session.notices[-1]
    assert "memory_reflection_mode: llm" in session.notices[-1]
    assert "interactive_approval: False" in session.notices[-1]


def test_chat_last_command_reports_latest_turn() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
        turns=[
            ChatTurn(
                prompt="inspect project",
                run_id="run_1",
                final_phase="done",
                tool_calls=1,
                selected_skills=["repo-onboarding"],
                summary="Inspected README.",
                phase="done",
                last_tool="read_file",
                last_tool_ok=True,
                last_tool_result="README.md",
            )
        ],
    )

    done = handle_chat_command("/last", session)

    assert done is False
    assert "prompt: inspect project" in session.notices[-1]
    assert "last_tool: read_file ok | README.md" in session.notices[-1]
    assert "selected_skills: repo-onboarding" in session.notices[-1]


def test_chat_last_command_reports_missing_turn() -> None:
    session = ChatSession(
        workspace=Path("."),
        model_name=None,
        model_base_url="https://api.openai.com/v1",
        no_model=True,
        llm_rerank=False,
        memory_reflection_mode="deterministic",
    )

    done = handle_chat_command("/last", session)

    assert done is False
    assert "(no completed turn yet)" in session.notices[-1]


def test_chat_memory_skills_trace_diff_preview_commands(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    memory = CliRunner().invoke(app, ["chat", "/memory", "--workspace", str(tmp_path), "--no-model", "--preview"])
    skills = CliRunner().invoke(app, ["chat", "/skills", "--workspace", str(tmp_path), "--no-model", "--preview"])
    trace = CliRunner().invoke(app, ["chat", "/trace", "--workspace", str(tmp_path), "--no-model", "--preview"])
    diff = CliRunner().invoke(app, ["chat", "/diff", "--workspace", str(tmp_path), "--no-model", "--preview"])
    tools = CliRunner().invoke(app, ["chat", "/tools", "--workspace", str(tmp_path), "--no-model", "--preview"])
    config = CliRunner().invoke(app, ["chat", "/config", "--workspace", str(tmp_path), "--no-model", "--preview"])
    last = CliRunner().invoke(app, ["chat", "/last", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert memory.exit_code == 0, memory.output
    assert "memory_backend:" in memory.output
    assert skills.exit_code == 0, skills.output
    assert "(no task to route yet)" in skills.output
    assert trace.exit_code == 0, trace.output
    assert "(no run trace yet)" in trace.output
    assert diff.exit_code == 0, diff.output
    assert "(no diff preview recorded)" in diff.output
    assert tools.exit_code == 0, tools.output
    assert "read_file risk=safe permission=allow" in tools.output
    assert config.exit_code == 0, config.output
    assert "memory_reflection_mode: deterministic" in config.output
    assert last.exit_code == 0, last.output
    assert "(no completed turn yet)" in last.output


def test_cli_write_preview_refuses_without_approval(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "write_file",
            "--workspace",
            str(tmp_path),
            "--path",
            "scratch.txt",
            "--content",
            "hello",
        ],
    )

    assert result.exit_code == 1
    assert "Preview:" in result.output
    assert "scratch.txt" in result.output
    assert not (tmp_path / "scratch.txt").exists()
