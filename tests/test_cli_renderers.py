from pathlib import Path

from minicode_agent.cli.renderers import (
    render_diff_summary,
    render_memory_summary,
    render_skill_route_summary,
    render_tool_summary,
    render_trace_summary,
)
from minicode_agent.skills.router import SkillRouteResult


def test_render_tool_summary_handles_unknown_status() -> None:
    assert render_tool_summary("read_file", None, "README.md") == "read_file unknown | README.md"


def test_render_trace_summary_handles_empty_events() -> None:
    text = render_trace_summary([], backend="sqlite", path=Path("trace.db"))

    assert "trace_backend: sqlite" in text
    assert "(no trace events)" in text


def test_render_diff_summary_handles_missing_preview() -> None:
    assert render_diff_summary(None) == "(no diff preview recorded)"


def test_render_memory_summary_handles_empty_records() -> None:
    text = render_memory_summary([], backend="sqlite", path=Path("memory.db"))

    assert "memory_backend: sqlite" in text
    assert "(no memories)" in text


def test_render_skill_route_summary_handles_no_candidates() -> None:
    result = SkillRouteResult(
        selected=[],
        candidates=[],
        no_match_reason="route debug",
    )

    text = render_skill_route_summary(result)

    assert "route debug" in text
    assert "No matching skills." in text
