from pathlib import Path

from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.skills import SkillError, SkillRegistry, SkillRouter
from minicode_agent.models import ModelResponse
from minicode_agent.skills.registry import parse_skill_metadata


def test_builtin_skill_registry_loads_expected_skills() -> None:
    registry = SkillRegistry()

    names = [skill.name for skill in registry.list()]

    assert names == ["code-review", "debugging", "test-writing"]
    debugging = registry.get("debugging")
    assert debugging.metadata.description.startswith("Diagnose")
    assert "失败" in debugging.metadata.aliases
    assert "Workflow:" in debugging.content


def test_skill_registry_reports_unknown_skill() -> None:
    registry = SkillRegistry()

    try:
        registry.get("missing")
    except SkillError as exc:
        assert str(exc) == "Unknown skill: missing"
    else:
        raise AssertionError("Expected SkillError")


def test_skill_registry_reports_invalid_metadata(tmp_path) -> None:
    skill_dir = tmp_path / "broken"
    skill_dir.mkdir()
    (skill_dir / "metadata.yaml").write_text("name: broken\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("# Broken\n", encoding="utf-8")
    registry = SkillRegistry(root=tmp_path)

    try:
        registry.list()
    except SkillError as exc:
        assert "Missing required metadata field 'description'" in str(exc)
    else:
        raise AssertionError("Expected SkillError")


def test_parse_skill_metadata_accepts_lists() -> None:
    metadata = parse_skill_metadata(
        """
        name: sample
        description: Sample skill.
        tags:
          - one
          - two
        applies_to:
          - demo
        examples:
          - example task
        aliases:
          - sample alias
        """,
        Path("metadata.yaml"),
    )

    assert metadata.name == "sample"
    assert metadata.tags == ["one", "two"]
    assert metadata.aliases == ["sample alias"]


def test_cli_skills_list_and_show() -> None:
    runner = CliRunner()

    list_result = runner.invoke(app, ["skills", "list"])
    show_result = runner.invoke(app, ["skills", "show", "debugging"])

    assert list_result.exit_code == 0, list_result.output
    assert "debugging" in list_result.output
    assert show_result.exit_code == 0, show_result.output
    assert "Diagnose failing behavior" in show_result.output
    assert "Workflow:" in show_result.output


def test_cli_skills_show_unknown_skill() -> None:
    result = CliRunner().invoke(app, ["skills", "show", "missing"])

    assert result.exit_code == 1
    assert "Unknown skill: missing" in result.output


def test_cli_skills_route_shows_scores() -> None:
    result = CliRunner().invoke(app, ["skills", "route", "审查 diff"])

    assert result.exit_code == 0, result.output
    assert "code-review" in result.output
    assert "*" in result.output


def test_skill_router_selects_debugging_or_test_writing_for_failing_tests() -> None:
    result = SkillRouter().route("修复 pytest failing tests")

    assert result.selected
    assert result.selected[0] in {"debugging", "test-writing"}
    assert result.reasons[result.selected[0]]


def test_skill_router_selects_code_review_for_diff_review() -> None:
    result = SkillRouter().route("review this diff for regressions")

    assert result.selected[0] == "code-review"
    assert any("review" in reason for reason in result.reasons["code-review"])


def test_skill_router_supports_chinese_aliases() -> None:
    debug_result = SkillRouter().route("修复测试失败")
    review_result = SkillRouter().route("审查 diff 风险")

    assert "debugging" in debug_result.selected
    assert "code-review" in review_result.selected


def test_skill_router_does_not_force_unrelated_skill() -> None:
    result = SkillRouter().route("list project files and inspect structure")

    assert result.selected == []
    assert result.candidates == []


def test_skill_router_can_llm_rerank_top_candidates() -> None:
    class RerankModel:
        def complete(self, messages):
            return ModelResponse(
                """
                {
                  "selected_skills": ["code-review", "debugging"],
                  "reason": "Review comes first for a diff-focused task."
                }
                """
            )

    router = SkillRouter(model_client=RerankModel(), enable_llm_rerank=True)

    result = router.route("review this diff for bugs")

    assert result.rerank_used
    assert not result.rerank_fallback
    assert result.selected[0] == "code-review"
    assert result.rerank_reason
