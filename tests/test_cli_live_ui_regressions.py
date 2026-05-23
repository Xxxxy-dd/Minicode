from minicode_agent.cli.live_ui import summarize_turn, wrap_text
from minicode_agent.core.state import AgentPhase, AgentState, TaskState


def test_summarize_turn_prefers_decision_over_history_summary() -> None:
    class Result:
        transcript = [{"event": "agent_planned", "payload": {"description": "Answering a simple identity question."}}]
        state = AgentState(
            run_id="run_1",
            workspace=".",
            user_goal="hello",
            current_phase=AgentPhase.DONE,
            task_state=TaskState(
                goal="hello",
                decisions=["I am MiniCode Agent."],
                history_summary="Answering a simple identity question.",
            ),
        )

    assert summarize_turn(Result()) == "I am MiniCode Agent."


def test_wrap_text_allows_long_prompt_folding() -> None:
    text = wrap_text("abcdefghijklmnopqrstuvwxyz" * 4, "#efe8dd")

    assert text.no_wrap is False
    assert text.overflow == "fold"
