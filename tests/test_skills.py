from pathlib import Path

from typer.testing import CliRunner

from minicode_agent.agent.planner import RuleBasedPlanner
from minicode_agent.cli.app import app
from minicode_agent.skills import SkillError, SkillRegistry, SkillRouter
from minicode_agent.models import ModelResponse
from minicode_agent.skills.registry import default_skill_registry, parse_skill_metadata


def test_builtin_skill_registry_loads_expected_skills() -> None:
    registry = SkillRegistry()

    names = [skill.name for skill in registry.list()]

    assert {"code-review", "debugging", "refactoring", "release-polish", "repo-onboarding", "security-review", "test-writing"} <= set(names)
    debugging = registry.get("debugging")
    assert debugging.metadata.description.startswith("Diagnose")
    assert "失败" in debugging.metadata.aliases
    assert "Workflow:" in debugging.content
    refactoring = registry.get("refactoring")
    assert "refactor" in refactoring.metadata.aliases
    assert "Workflow:" in refactoring.content


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


def test_default_skill_registry_loads_env_and_workspace_skills(tmp_path, monkeypatch) -> None:
    env_root = tmp_path / "env-skills"
    workspace = tmp_path / "workspace"
    write_skill(env_root, "env-helper", "External helper skill.")
    write_skill(workspace / ".minicode" / "skills", "workspace-helper", "Workspace helper skill.")
    monkeypatch.setenv("MINICODE_SKILL_PATHS", str(env_root))

    registry = default_skill_registry(workspace)
    names = [skill.name for skill in registry.list()]

    assert "debugging" in names
    assert "env-helper" in names
    assert "workspace-helper" in names


def test_workspace_skill_overrides_external_and_builtin(tmp_path, monkeypatch) -> None:
    env_root = tmp_path / "env-skills"
    workspace = tmp_path / "workspace"
    write_skill(env_root, "debugging", "External debugging skill.")
    write_skill(workspace / ".minicode" / "skills", "debugging", "Workspace debugging skill.")
    monkeypatch.setenv("MINICODE_SKILL_PATHS", str(env_root))

    skill = default_skill_registry(workspace).get("debugging")

    assert skill.metadata.description == "Workspace debugging skill."
    assert skill.path == workspace / ".minicode" / "skills" / "debugging"


def test_parse_skill_metadata_accepts_lists() -> None:
    metadata = parse_skill_metadata(
        """
        schema_version: 1
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
    assert metadata.schema_version == 1
    assert metadata.tags == ["one", "two"]
    assert metadata.aliases == ["sample alias"]


def test_parse_skill_metadata_rejects_unsupported_schema_version() -> None:
    try:
        parse_skill_metadata(
            """
            schema_version: 2
            name: sample
            description: Sample skill.
            tags:
              - one
            applies_to:
              - demo
            examples:
              - example task
            """,
            Path("metadata.yaml"),
        )
    except SkillError as exc:
        assert "Unsupported skill metadata schema_version 2" in str(exc)
    else:
        raise AssertionError("Expected SkillError")


def test_parse_skill_metadata_rejects_non_string_list_items() -> None:
    try:
        parse_skill_metadata(
            """
            name: sample
            description: Sample skill.
            tags:
              - 123
            applies_to:
              - demo
            examples:
              - example task
            """,
            Path("metadata.yaml"),
        )
    except SkillError as exc:
        assert "Metadata field 'tags' must be a list of strings" in str(exc)
    else:
        raise AssertionError("Expected SkillError")


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
    assert "candidate" in result.output


def test_cli_skills_route_shows_no_match_reason() -> None:
    result = CliRunner().invoke(app, ["skills", "route", "say hello and wait"])

    assert result.exit_code == 0, result.output
    assert "No matching skills" in result.output
    assert "No skill metadata matched" in result.output


def test_cli_skills_route_accepts_llm_rerank_without_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MINICODE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    result = CliRunner().invoke(app, ["skills", "route", "review this diff", "--workspace", str(tmp_path), "--llm-rerank"])

    assert result.exit_code == 0, result.output
    assert "code-review" in result.output
    assert "rerank skipped" in result.output
    assert "no model client is configured" in result.output


def test_cli_skills_list_includes_workspace_skills(tmp_path) -> None:
    write_skill(tmp_path / ".minicode" / "skills", "local-helper", "Local helper skill.")

    result = CliRunner().invoke(app, ["skills", "list", "--workspace", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "local-helper" in result.output


def test_skill_router_selects_debugging_or_test_writing_for_failing_tests() -> None:
    result = SkillRouter().route("修复 pytest failing tests")

    assert result.selected
    assert result.selected[0] in {"debugging", "test-writing"}
    assert result.reasons[result.selected[0]]


def test_skill_router_selects_code_review_for_diff_review() -> None:
    result = SkillRouter().route("review this diff for regressions")

    assert result.selected[0] == "code-review"
    assert any("review" in reason for reason in result.reasons["code-review"])


def test_skill_router_selects_v11_builtin_skills() -> None:
    router = SkillRouter()

    refactor = router.route("refactor duplicate code without changing behavior")
    release = router.route("prepare V1.1 release")
    security = router.route("审查 dangerous command and secret handling")
    onboarding = router.route("inspect repo and explain project structure")

    assert refactor.selected[0] == "refactoring"
    assert release.selected[0] == "release-polish"
    assert security.selected[0] == "security-review"
    assert onboarding.selected[0] == "repo-onboarding"


def test_skill_router_supports_chinese_aliases() -> None:
    debug_result = SkillRouter().route("修复测试失败")
    review_result = SkillRouter().route("审查 diff 风险")

    assert "debugging" in debug_result.selected
    assert "code-review" in review_result.selected


def test_skill_router_selects_repo_onboarding_for_project_structure() -> None:
    result = SkillRouter().route("list project files and inspect structure")

    assert result.selected == ["repo-onboarding"]
    assert result.reasons["repo-onboarding"]


def test_skill_router_does_not_force_unrelated_skill() -> None:
    result = SkillRouter().route("say hello and wait for the user")

    assert result.selected == []
    assert result.candidates == []
    assert result.no_match_reason
    assert "No skill metadata matched" in result.debug_summary


def test_skill_router_does_not_route_plain_readme_read_as_release_polish() -> None:
    result = SkillRouter().route("read README.md")

    assert "release-polish" not in result.selected


def test_skill_route_exposes_unselected_candidate_reasons() -> None:
    result = SkillRouter().route("review this diff for security risks")

    assert result.selected
    assert result.unselected_reasons
    assert all(name not in result.selected for name in result.unselected_reasons)


def test_rule_based_planner_delegates_skill_selection_to_router() -> None:
    planner = RuleBasedPlanner()

    assert planner.select_skill("prepare V1.1 release") == "release-polish"
    assert planner.select_skill("refactor duplicate code") == "refactoring"


def test_rule_based_planner_can_use_injected_skill_router(tmp_path) -> None:
    write_skill(tmp_path, "local-release", "Local release helper skill.")
    router = SkillRouter(SkillRegistry(root=tmp_path))

    planner = RuleBasedPlanner(router)

    assert planner.select_skill("use local-release") == "local-release"


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


def test_skill_router_explains_skipped_llm_rerank_without_model() -> None:
    result = SkillRouter(enable_llm_rerank=True).route("review this diff")

    assert not result.rerank_used
    assert result.rerank_skipped_reason == "LLM rerank requested but no model client is configured."


def write_skill(root: Path, name: str, description: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "metadata.yaml").write_text(
        f"""
        name: {name}
        description: {description}
        tags:
          - helper
        applies_to:
          - helper task
        examples:
          - use {name}
        aliases:
          - {name}
        """,
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(f"# {name}\n\nUse this skill for helper tasks.\n", encoding="utf-8")
    return skill_dir
