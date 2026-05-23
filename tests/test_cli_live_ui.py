from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.cli.live_ui import ChatSession, ChatTurn, render_bottom_panel, render_conversation_area, render_top_panel


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
    console.print(render_bottom_panel(session, 120))
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
    assert "─" in result.output


def test_help_command_shows_a_system_notice() -> None:
    result = CliRunner().invoke(app, ["chat", "/help", "--workspace", ".", "--no-model", "--preview"])

    assert result.exit_code == 0, result.output
    assert "SYSTEM" in result.output
    assert "Shortcuts:" in result.output
