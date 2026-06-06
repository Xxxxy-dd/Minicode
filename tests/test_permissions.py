from pathlib import Path

from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.permissions.policy import CommandSafetyClassifier, PathSandbox, PermissionPolicy, SensitivePathPolicy
from minicode_agent.tools.base import BaseTool
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import ToolRegistry, create_default_registry
from minicode_agent.tools.types import DuplicatePolicy, PermissionMode, RiskLevel, ToolContext, ToolIntent, ToolSpec, ToolStateEffect


class MediumRiskTool(BaseTool):
    spec = ToolSpec(
        name="medium_risk",
        description="A test tool requiring approval.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
    )

    def __init__(self) -> None:
        self.called = False

    def _run(self, context: ToolContext, arguments: dict) -> tuple[str, dict]:
        self.called = True
        return "approved run", {}


class BlockedTool(BaseTool):
    spec = ToolSpec(
        name="blocked_tool",
        description="A blocked test tool.",
        risk_level=RiskLevel.BLOCKED,
        permission=PermissionMode.DENY,
    )

    def __init__(self) -> None:
        self.called = False

    def _run(self, context: ToolContext, arguments: dict) -> tuple[str, dict]:
        self.called = True
        return "should not run", {}


def test_path_sandbox_allows_workspace_path(tmp_path) -> None:
    decision = PathSandbox(tmp_path).validate_path("README.md")

    assert decision.mode == PermissionMode.ALLOW


def test_path_sandbox_denies_escape(tmp_path) -> None:
    decision = PathSandbox(tmp_path).validate_path("../outside.txt")

    assert decision.mode == PermissionMode.DENY
    assert "escapes workspace" in decision.reason


def test_permission_policy_checks_path_arguments(tmp_path) -> None:
    tool = create_default_registry().get("read_file")

    decision = PermissionPolicy().decide(tool.spec, arguments={"path": "../outside.txt"}, workspace=tmp_path)

    assert decision.mode == PermissionMode.DENY


def test_sensitive_path_policy_blocks_env_and_ssh_paths() -> None:
    policy = SensitivePathPolicy()

    assert policy.validate_path(".env").mode == PermissionMode.DENY
    assert policy.validate_path(".ssh/id_ed25519").mode == PermissionMode.DENY
    assert policy.validate_path("config.pem").mode == PermissionMode.DENY
    assert policy.validate_path(".env.example").mode == PermissionMode.ALLOW


def test_permission_policy_sensitive_path_read_write_matrix(tmp_path) -> None:
    registry = create_default_registry()
    policy = PermissionPolicy()

    read_env = policy.decide(registry.get("read_file").spec, arguments={"path": ".env"}, workspace=tmp_path)
    write_env = policy.decide(registry.get("write_file").spec, arguments={"path": ".env"}, workspace=tmp_path)
    read_example = policy.decide(registry.get("read_file").spec, arguments={"path": ".env.example"}, workspace=tmp_path)

    assert read_env.mode == PermissionMode.DENY
    assert write_env.mode == PermissionMode.DENY
    assert read_example.mode == PermissionMode.ALLOW


def test_command_classifier_blocks_windows_destructive_commands() -> None:
    classifier = CommandSafetyClassifier()

    assert classifier.classify("Remove-Item . -Recurse -Force").mode == PermissionMode.DENY
    assert classifier.classify("rd /s build").mode == PermissionMode.DENY
    assert classifier.classify("reg delete HKCU\\Software\\Demo").mode == PermissionMode.DENY


def test_permission_policy_respects_explicit_ask(tmp_path) -> None:
    tool = ToolSpec(
        name="explicit_ask",
        description="Explicit ask tool.",
        risk_level=RiskLevel.LOW,
        permission=PermissionMode.ASK,
    )

    decision = PermissionPolicy().decide(tool, workspace=tmp_path)

    assert decision.mode == PermissionMode.ASK


def test_permission_policy_respects_explicit_deny(tmp_path) -> None:
    tool = ToolSpec(
        name="explicit_deny",
        description="Explicit deny tool.",
        risk_level=RiskLevel.SAFE,
        permission=PermissionMode.DENY,
    )

    decision = PermissionPolicy().decide(tool, workspace=tmp_path)

    assert decision.mode == PermissionMode.DENY


def test_default_tool_duplicate_policies_are_declared() -> None:
    registry = create_default_registry()
    block_identical = {
        "append_file",
        "apply_patch",
        "create_file",
        "delete_file",
        "edit_file",
        "git_diff",
        "git_status",
        "inspect_repo",
        "list_files",
        "read_file",
        "search_code",
        "write_file",
    }

    for tool in registry.list():
        expected = DuplicatePolicy.BLOCK_IDENTICAL_SUCCESS if tool.spec.name in block_identical else DuplicatePolicy.ALLOW
        assert tool.spec.duplicate_policy == expected


def test_default_tool_cross_cutting_policies_are_declared() -> None:
    registry = create_default_registry()

    assert ToolStateEffect.MARKS_MODIFIED_FILE in registry.get("write_file").spec.state_effects
    assert ToolStateEffect.MARKS_MODIFIED_FILE in registry.get("append_file").spec.state_effects
    assert ToolStateEffect.MARKS_MODIFIED_FILE in registry.get("create_file").spec.state_effects
    assert ToolStateEffect.MARKS_MODIFIED_FILE in registry.get("delete_file").spec.state_effects
    assert ToolStateEffect.MARKS_MODIFIED_FILE in registry.get("edit_file").spec.state_effects
    assert ToolStateEffect.RECORDS_PATH_FACT in registry.get("read_file").spec.state_effects
    assert ToolIntent.FILE_OVERWRITE in registry.get("write_file").spec.intents
    assert ToolIntent.FILE_READ in registry.get("read_file").spec.intents
    assert ToolIntent.FILE_SEARCH in registry.get("search_code").spec.intents
    assert ToolIntent.FILE_APPEND in registry.get("append_file").spec.intents
    assert ToolIntent.FILE_CREATE in registry.get("create_file").spec.intents
    assert ToolIntent.FILE_DELETE in registry.get("delete_file").spec.intents
    assert ToolIntent.FILE_EDIT in registry.get("edit_file").spec.intents
    assert ToolIntent.FILE_EDIT in registry.get("apply_patch").spec.intents
    assert ToolIntent.REPO_INSPECT in registry.get("inspect_repo").spec.intents
    assert ToolIntent.COMMAND_RUN in registry.get("run_formatter").spec.intents
    assert ToolIntent.COMMAND_RUN in registry.get("run_linter").spec.intents
    assert registry.get("read_file").spec.path_arg_names == ("path",)
    assert registry.get("run_formatter").spec.command_arg_names == ("command", "argv")
    assert registry.get("inspect_repo").spec.input_schema["properties"]["max_files"]["default"] == 200
    assert registry.get("apply_patch").spec.input_schema["anyOf"]
    assert registry.get("run_formatter").spec.input_schema["anyOf"]
    assert registry.get("run_linter").spec.input_schema["anyOf"]
    assert registry.get("read_file").spec.capture_full_output is True
    assert registry.get("spawn_subagent").spec.counts_as_subagent_call is True
    assert registry.get("read_file").spec.subagent_roles == ("explorer", "reviewer", "security-reviewer")
    assert registry.get("run_tests").spec.subagent_roles == ("tester",)
    assert registry.get("write_file").spec.subagent_roles == ()


def test_executor_denies_workspace_escape_before_tool_runs(tmp_path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    executor = ToolExecutor(create_default_registry())

    observation = executor.execute("read_file", ToolContext(workspace=tmp_path), {"path": "../outside.txt"})

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.DENY.value
    assert "escapes workspace" in observation.error


def test_executor_stops_medium_risk_tool_for_approval(tmp_path) -> None:
    registry = ToolRegistry()
    tool = MediumRiskTool()
    registry.register(tool)

    observation = ToolExecutor(registry).execute("medium_risk", ToolContext(workspace=tmp_path))

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.ASK.value
    assert "approval" in observation.error
    assert not tool.called


def test_executor_runs_ask_tool_after_approval(tmp_path) -> None:
    registry = ToolRegistry()
    tool = MediumRiskTool()
    registry.register(tool)

    observation = ToolExecutor(registry).execute("medium_risk", ToolContext(workspace=tmp_path), approved=True)

    assert observation.ok
    assert observation.output == "approved run"
    assert observation.metadata["permission"] == PermissionMode.ALLOW.value
    assert observation.metadata["approved"] is True
    assert tool.called


def test_executor_denies_blocked_tool(tmp_path) -> None:
    registry = ToolRegistry()
    tool = BlockedTool()
    registry.register(tool)

    observation = ToolExecutor(registry).execute("blocked_tool", ToolContext(workspace=tmp_path))

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.DENY.value
    assert "denied" in observation.error
    assert not tool.called


def test_cli_denies_workspace_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tools", "run", "read_file", "--workspace", str(tmp_path), "--path", "../outside.txt"],
    )

    assert result.exit_code == 1
    assert "escapes workspace" in result.output
