import sys

from typer.testing import CliRunner

from minicode_agent.cli.app import app
from minicode_agent.permissions.policy import CommandSafetyClassifier
from minicode_agent.tools.executor import ToolExecutor
from minicode_agent.tools.registry import create_default_registry
from minicode_agent.tools.types import PermissionMode, ToolContext


def execute(name: str, workspace, arguments: dict | None = None, approved: bool = False):
    return ToolExecutor(create_default_registry()).execute(
        name,
        ToolContext(workspace=workspace),
        arguments or {},
        approved=approved,
    )


def python_argv(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def python_version_command() -> str:
    return f"{sys.executable} -V"


def echo_command() -> str:
    return "cmd /c echo hello"


def test_command_classifier_blocks_dangerous_delete() -> None:
    decision = CommandSafetyClassifier().classify("rm -rf .")

    assert decision.mode == PermissionMode.DENY


def test_run_shell_requires_approval(tmp_path) -> None:
    observation = execute("run_shell", tmp_path, {"argv": python_argv("print('hello')")})

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.ASK.value


def test_run_shell_executes_after_approval(tmp_path) -> None:
    observation = execute(
        "run_shell",
        tmp_path,
        {"argv": python_argv("print('hello')")},
        approved=True,
    )

    assert observation.ok
    assert "hello" in observation.output
    assert observation.metadata["exit_code"] == 0


def test_run_shell_blocks_dangerous_command_even_when_approved(tmp_path) -> None:
    observation = execute("run_shell", tmp_path, {"command": "rm -rf ."}, approved=True)

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.DENY.value
    assert "blocked" in observation.error


def test_run_shell_blocks_dangerous_argv_even_when_approved(tmp_path) -> None:
    observation = execute("run_shell", tmp_path, {"argv": ["git", "push"]}, approved=True)

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.DENY.value
    assert "blocked" in observation.error


def test_run_shell_reports_timeout(tmp_path) -> None:
    observation = execute(
        "run_shell",
        tmp_path,
        {"argv": python_argv("import time; time.sleep(2)"), "timeout_seconds": 1},
        approved=True,
    )

    assert observation.ok
    assert observation.metadata["timed_out"] is True


def test_run_tests_defaults_to_pytest_after_approval(tmp_path) -> None:
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    observation = execute("run_tests", tmp_path, approved=True)

    assert observation.ok
    assert observation.metadata["exit_code"] == 0
    assert "passed" in observation.output


def test_run_formatter_requires_explicit_command_and_approval(tmp_path) -> None:
    observation = execute("run_formatter", tmp_path, {"argv": python_argv("print('styled')")})

    assert not observation.ok
    assert observation.metadata["permission"] == PermissionMode.ASK.value


def test_run_formatter_executes_after_approval(tmp_path) -> None:
    observation = execute(
        "run_formatter",
        tmp_path,
        {"argv": python_argv("print('styled')")},
        approved=True,
    )

    assert observation.ok
    assert "styled" in observation.output
    assert observation.metadata["quality_tool"] == "formatter"


def test_run_linter_executes_after_approval(tmp_path) -> None:
    observation = execute(
        "run_linter",
        tmp_path,
        {"argv": python_argv("print('lint')")},
        approved=True,
    )

    assert observation.ok
    assert "lint" in observation.output
    assert observation.metadata["quality_tool"] == "linter"


def test_cli_run_shell_requires_approval(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        ["tools", "run", "run_shell", "--workspace", str(tmp_path), "--command", echo_command()],
    )

    assert result.exit_code == 1
    assert "requires approval" in result.output


def test_cli_run_shell_after_approval(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "run_shell",
            "--workspace",
            str(tmp_path),
            "--command",
            echo_command(),
            "--approved",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "hello" in result.output


def test_cli_run_shell_with_argv_after_approval(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "run_shell",
            "--workspace",
            str(tmp_path),
            "--arg",
            "cmd",
            "--arg",
            "/c",
            "--arg",
            "echo hello",
            "--approved",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "hello" in result.output


def test_cli_run_shell_blocks_dangerous_argv(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "tools",
            "run",
            "run_shell",
            "--workspace",
            str(tmp_path),
            "--arg",
            "git",
            "--arg",
            "push",
            "--approved",
        ],
    )

    assert result.exit_code == 1
    assert "blocked" in result.output
