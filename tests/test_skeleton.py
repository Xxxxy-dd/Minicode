from pathlib import Path

from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.config import MiniCodeConfig
from minicode_agent.core.state import AgentPhase, AgentState, TaskState
from minicode_agent.permissions.policy import PermissionPolicy
from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolSpec


def test_config_normalizes_workspace() -> None:
    config = MiniCodeConfig(workspace=Path("."))

    assert config.workspace.is_absolute()


def test_agent_state_defaults() -> None:
    state = AgentState(
        run_id="run_1",
        workspace=".",
        user_goal="fix tests",
        task_state=TaskState(goal="fix tests"),
    )

    assert state.current_phase == AgentPhase.INIT
    assert state.metrics.tool_calls == 0


def test_permission_policy_blocks_blocked_tools() -> None:
    tool = ToolSpec(
        name="dangerous",
        description="dangerous command",
        risk_level=RiskLevel.BLOCKED,
        permission=PermissionMode.DENY,
    )

    decision = PermissionPolicy().decide(tool)

    assert decision.mode == PermissionMode.DENY


def test_cli_run_executes_loop() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "fix tests"])

    assert result.exit_code == 0
    assert "run_id: agent_" in result.output
    assert "Final phase: done" in result.output
