import json
import subprocess
from pathlib import Path

from minicode_agent.agent import AgentLoop
from minicode_agent.cli.app import app
from minicode_agent.core.state import AgentPhase
from minicode_agent.runtime import RuntimeContext
from minicode_agent.subagents import AgentTeam, SubagentRequest, SubagentRole, SubagentRunner, WorktreeManager
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import ToolContext


def init_git_repo(path, filename: str = "tracked.txt") -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / filename).write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_registry_includes_spawn_subagent() -> None:
    names = {tool.spec.name for tool in create_default_registry().list()}

    assert "spawn_subagent" in names


def test_explorer_subagent_reads_limited_context(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\nneedle\n", encoding="utf-8")
    runner = SubagentRunner(tmp_path)

    result = runner.run(
        SubagentRequest(
            role=SubagentRole.EXPLORER,
            task="find needle",
            pattern="needle",
            max_steps=3,
        )
    )

    assert result.ok
    assert result.role == SubagentRole.EXPLORER
    assert result.tool_calls == 1
    assert "README.md" in result.findings[0]
    assert "search_code" in result.allowed_tools
    assert "spawn_subagent" in result.denied_tools


def test_explorer_subagent_supports_path_and_pattern(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\nneedle\n", encoding="utf-8")

    result = SubagentRunner(tmp_path).run(
        SubagentRequest(
            role=SubagentRole.EXPLORER,
            task="read and find needle",
            path="README.md",
            pattern="needle",
            max_steps=3,
        )
    )

    assert result.tool_calls == 2
    assert result.findings[0].startswith("read_file:")
    assert result.findings[1].startswith("search_code:")


def test_reviewer_subagent_reviews_git_diff(tmp_path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("hello again\n", encoding="utf-8")

    result = SubagentRunner(tmp_path).run(
        SubagentRequest(role=SubagentRole.REVIEWER, task="review current diff", max_steps=2)
    )

    assert result.ok
    assert result.tool_calls == 2
    assert "hello again" in "\n".join(result.findings)
    assert result.allowed_tools == ["git_diff", "git_status", "read_file", "search_code"]
    assert "spawn_subagent" in result.denied_tools
    assert result.changed_files == ["tracked.txt"]
    assert result.evidence
    assert result.risks == ["Reviewer did not find test evidence in the collected diff/status."]
    assert result.test_suggestions == ["Run the relevant test suite before merging."]


def test_subagent_respects_max_steps(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    result = SubagentRunner(tmp_path).run(
        SubagentRequest(role=SubagentRole.EXPLORER, task="inspect project", max_steps=1)
    )

    assert result.tool_calls == 1
    assert result.stopped_reason == "max_steps"


def test_spawn_subagent_cannot_use_write_tools(tmp_path) -> None:
    executor = ToolExecutor(create_default_registry(), run_id="subagent_permission_test")

    observation = executor.execute(
        "spawn_subagent",
        ToolContext(workspace=tmp_path),
        {"role": "explorer", "task": "try to write", "tool": "write_file", "max_steps": 1},
    )

    assert observation.ok
    payload = json.loads(observation.output)
    assert payload["tool_calls"] == 1
    assert "write_file" not in payload["allowed_tools"]
    assert "spawn_subagent" in payload["denied_tools"]
    assert not (tmp_path / "notes.txt").exists()


def test_spawn_subagent_tool_metadata_includes_role_limits(tmp_path) -> None:
    executor = ToolExecutor(create_default_registry(), run_id="subagent_metadata_test")

    observation = executor.execute(
        "spawn_subagent",
        ToolContext(workspace=tmp_path),
        {"role": "explorer", "task": "inspect project", "max_steps": 1},
    )

    assert observation.ok
    assert observation.metadata["role"] == "explorer"
    assert observation.metadata["max_steps"] == 1
    assert observation.metadata["team_id"].startswith("team_")
    assert observation.metadata["team_workspace_plan"]["will_create_worktree"] is False
    assert "list_files" in observation.metadata["allowed_tools"]
    assert "spawn_subagent" in observation.metadata["denied_tools"]


def test_tester_subagent_runs_approved_test_tool(tmp_path) -> None:
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    result = SubagentRunner(tmp_path).run(
        SubagentRequest(role=SubagentRole.TESTER, task="run tests", max_steps=1)
    )

    assert result.ok
    assert result.allowed_tools == ["run_tests"]
    assert result.test_results
    assert result.test_results[0]["passed"] is True
    assert result.evidence[0]["metadata"]["approved"] is True


def test_cli_tools_run_spawn_subagent(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")

    from typer.testing import CliRunner

    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "spawn_subagent",
            "--workspace",
            str(tmp_path),
            "--role",
            "explorer",
            "--task",
            "inspect project",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"role": "explorer"' in result.output
    assert '"tool_calls"' in result.output


def test_agent_loop_review_task_uses_reviewer_subagent(tmp_path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("hello again\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_subagent_test")

    result = AgentLoop(runtime, "review current diff").run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.subagent_calls == 1
    events = runtime.trace_store.list_events("agent_subagent_test")
    event_types = [event.event_type for event in events]
    assert "subagent_started" in event_types
    assert "subagent_finished" in event_types
    assert "team_started" in event_types
    assert "team_role_started" in event_types
    assert "team_role_completed" in event_types
    assert "team_finished" in event_types


def test_agent_loop_does_not_trigger_reviewer_for_generic_review_word(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    runtime = RuntimeContext.create(tmp_path, run_id="agent_generic_review_test")

    result = AgentLoop(runtime, "review README style").run()

    assert result.state.current_phase == AgentPhase.DONE
    assert result.state.metrics.subagent_calls == 0


def test_agent_team_records_workspace_plan_and_merge_blocker(tmp_path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("hello again\n", encoding="utf-8")

    team = AgentTeam(tmp_path).run(
        "review diff",
        [SubagentRequest(role=SubagentRole.REVIEWER, task="review diff", max_steps=2)],
    )

    assert team.team_id.startswith("team_")
    assert team.workspace_plan.repo_detected
    assert team.workspace_plan.dirty
    assert not team.workspace_plan.will_create_worktree
    assert not team.workspace_plan.will_merge
    assert team.results[0].role == SubagentRole.REVIEWER
    assert "automatic merge is disabled; central agent must review any patch proposal" in team.results[0].merge_blockers
    assert team.report is not None
    assert team.report.evidence
    assert team.report.merge_blockers


def test_implementer_requires_clean_worktree(tmp_path) -> None:
    team = AgentTeam(tmp_path).run(
        "implement change",
        [SubagentRequest(role=SubagentRole.IMPLEMENTER, task="implement change", max_steps=1)],
    )

    assert not team.ok
    assert team.results[0].tool_calls == 0
    assert team.results[0].stopped_reason == "worktree_blocked"
    assert team.results[0].merge_blockers


def test_agent_team_runs_role_profiles(tmp_path) -> None:
    init_git_repo(tmp_path)

    team = AgentTeam(tmp_path).run(
        "inspect and test",
        [
            SubagentRequest(role=SubagentRole.EXPLORER, task="inspect repo", max_steps=1),
            SubagentRequest(role=SubagentRole.TESTER, task="run tests", max_steps=1),
            SubagentRequest(role=SubagentRole.SECURITY_REVIEWER, task="review security", max_steps=2),
        ],
    )

    assert [profile.role for profile in team.roles] == [
        SubagentRole.EXPLORER,
        SubagentRole.TESTER,
        SubagentRole.SECURITY_REVIEWER,
    ]
    assert team.roles[1].allowed_tools == ["run_tests"]
    assert team.roles[2].evidence_contract.required_fields == ["security_findings", "evidence", "merge_blockers"]


def test_team_report_contains_evidence_contract(tmp_path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("hello again\n", encoding="utf-8")

    team = AgentTeam(tmp_path).run(
        "review diff",
        [SubagentRequest(role=SubagentRole.REVIEWER, task="review diff", max_steps=2)],
    )

    assert team.report is not None
    assert team.roles[0].evidence_contract.required_fields == [
        "changed_files",
        "risks",
        "test_suggestions",
        "evidence",
        "merge_blockers",
    ]
    assert team.report.tool_summary == {"reviewer": 2}
    assert any(item["type"] == "workspace_plan" for item in team.report.evidence)


def test_worktree_manager_creates_isolated_clean_workspace(tmp_path) -> None:
    init_git_repo(tmp_path)
    manager = WorktreeManager(tmp_path)

    plan = manager.create(manager.plan(requested=True))

    try:
        assert plan.created_worktree
        assert plan.worktree_path
        worktree = json.loads(json.dumps(plan.model_dump()))["worktree_path"]
        assert Path(worktree).exists()
        assert subprocess.run(["git", "status", "--short"], cwd=worktree, capture_output=True, text=True).returncode == 0
    finally:
        manager.cleanup(plan)


def test_worktree_manager_blocks_dirty_workspace(tmp_path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    manager = WorktreeManager(tmp_path)

    plan = manager.plan(requested=True)

    assert plan.dirty
    assert not plan.can_create_worktree
    assert not plan.created_worktree
    assert "dirty worktree detected" in plan.reason


def test_worktree_patch_proposal_does_not_merge_automatically(tmp_path) -> None:
    init_git_repo(tmp_path)

    team = AgentTeam(tmp_path).run(
        "implement change",
        [SubagentRequest(role=SubagentRole.IMPLEMENTER, task="implement change", max_steps=1)],
    )

    try:
        assert team.ok
        assert team.workspace_plan.created_worktree
        assert team.results[0].patch_proposal is not None
        assert team.results[0].patch_proposal["will_merge"] is False
        assert "patch proposal requires central review; automatic merge is disabled" in team.results[0].merge_blockers
        assert subprocess.run(["git", "status", "--short"], cwd=tmp_path, capture_output=True, text=True).stdout == ""
    finally:
        WorktreeManager(tmp_path).cleanup(team.workspace_plan)


def test_team_report_links_tester_result_to_patch_proposal(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "tracked.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    team = AgentTeam(tmp_path).run(
        "implement and test",
        [
            SubagentRequest(role=SubagentRole.IMPLEMENTER, task="prepare patch proposal", max_steps=1),
            SubagentRequest(role=SubagentRole.TESTER, task="run tests", max_steps=1),
        ],
    )

    try:
        assert team.report is not None
        proposal = team.report.patch_proposals[0]
        assert proposal.proposal_id.startswith("proposal_")
        assert proposal.test_result is not None
        assert proposal.test_result["passed"] is True
    finally:
        WorktreeManager(tmp_path).cleanup(team.workspace_plan)


def test_worktree_review_context_links_reviewer_to_patch_proposal(tmp_path) -> None:
    init_git_repo(tmp_path)

    team = AgentTeam(tmp_path).run(
        "implement and review",
        [
            SubagentRequest(role=SubagentRole.IMPLEMENTER, task="prepare patch proposal", max_steps=1),
            SubagentRequest(role=SubagentRole.REVIEWER, task="review patch proposal", max_steps=2),
        ],
    )

    try:
        reviewer = next(result for result in team.results if result.role == SubagentRole.REVIEWER)
        proposal = next(result.patch_proposal for result in team.results if result.patch_proposal)
        context = next(item for item in reviewer.evidence if item.get("type") == "worktree_review_context")
        assert context["proposal_id"] == proposal["proposal_id"]
        assert context["worktree_path"] == team.workspace_plan.worktree_path
    finally:
        WorktreeManager(tmp_path).cleanup(team.workspace_plan)


def test_worktree_retention_is_traced(tmp_path) -> None:
    init_git_repo(tmp_path)
    runtime = RuntimeContext.create(tmp_path, run_id="worktree_retention_test")

    team = AgentTeam(
        tmp_path,
        trace_store=runtime.trace_store,
        parent_run_id=runtime.run_id,
    ).run(
        "implement change",
        [SubagentRequest(role=SubagentRole.IMPLEMENTER, task="implement change", max_steps=1)],
    )

    try:
        events = runtime.trace_store.list_events(runtime.run_id)
        retained = [event for event in events if event.event_type == "worktree_retained"]
        assert retained
        assert retained[0].payload["worktree_path"] == team.workspace_plan.worktree_path
        assert retained[0].payload["cleanup_policy"] == "manual"
    finally:
        WorktreeManager(tmp_path).cleanup(team.workspace_plan)
