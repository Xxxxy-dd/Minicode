from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.cli.live_ui import (
    ChatSession,
    ChatTurn,
    build_approval_callback,
    format_stream_event,
    input_bar,
    render_bottom_panel,
    render_conversation_area,
    render_chat_intro,
    render_latest_turn,
    refresh_chat_intro,
    render_top_panel,
    summarize_turn,
    wrap_text,
)
from minicode_agent.core.state import AgentPhase, AgentState, TaskState


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
    assert "tools:" not in text
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


def test_help_command_shows_a_system_notice() -> None:
    result = CliRunner().invoke(app, ["chat", "/help", "--workspace", ".", "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "SYSTEM" in result.output
    assert "Shortcuts:" in result.output


def test_interactive_input_bar_has_focus_rules() -> None:
    result = CliRunner().invoke(app, ["chat", "--workspace", ".", "--no-model"], input="/exit\n")

    assert result.exit_code == 0, result.output
    assert "Command" in result.output
    assert "? for shortcuts" not in result.output
    assert "UnboundLocalError" not in result.output


def test_input_bar_does_not_render_extra_rules(monkeypatch) -> None:
    console = Console(record=True, width=40)
    monkeypatch.setattr("minicode_agent.cli.live_ui.Console", lambda: console)
    monkeypatch.setattr(console, "input", lambda prompt: (console.print(prompt), "hello")[1])

    value, panel_height = input_bar()
    assert value == "hello"
    assert panel_height > 0
    text = console.export_text()
    assert "Command" in text
    assert "Enter a task" in text
    assert "> " in text


def test_format_stream_event_shows_phase_and_tool() -> None:
    phase = format_stream_event("phase_changed", {"phase": "plan", "reason": "Draft a short plan."})
    tool = format_stream_event("action_result", {"tool": "read_file", "ok": True, "result": "README.md"})

    assert phase is not None
    assert "plan" in phase.plain
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


def test_direct_chat_query_is_answered_without_agent_loop(tmp_path) -> None:
    result = CliRunner().invoke(app, ["chat", "你有什么工具", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "我可以读写文件" in result.output or "我有文件读取" in result.output
    assert "phase init" not in result.output


def test_chat_task_still_runs_agent_loop_for_workspace_work(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["chat", "inspect project", "--workspace", str(tmp_path), "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "phase init" not in result.output
    assert "MiniCode Agent" in result.output
