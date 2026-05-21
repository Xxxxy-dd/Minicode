import shlex
import subprocess
import sys
from typing import Any

from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolContext, ToolSpec

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_CHARS = 20000


class RunShellTool(BaseTool):
    spec = ToolSpec(
        name="run_shell",
        description="Run an approved command in the workspace without shell expansion.",
        risk_level=RiskLevel.HIGH,
        permission=PermissionMode.ASK,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        command = arguments.get("command")
        argv = arguments.get("argv")
        if not command and not argv:
            raise ToolError("Missing required argument: command")
        timeout = int(arguments.get("timeout_seconds") or self.spec.timeout_seconds)
        return run_command(str(command) if command else None, context, timeout, argv=argv)


class RunTestsTool(BaseTool):
    spec = ToolSpec(
        name="run_tests",
        description="Run an approved test command in the workspace.",
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        timeout_seconds=60,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        command = arguments.get("command")
        argv = arguments.get("argv") or ([sys.executable, "-m", "pytest"] if command is None else None)
        timeout = int(arguments.get("timeout_seconds") or self.spec.timeout_seconds)
        return run_command(str(command) if command else None, context, timeout, argv=argv)


def run_command(
    command: str | None,
    context: ToolContext,
    timeout_seconds: int,
    argv: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    argv = argv or split_command(command or "")
    if not argv:
        raise ToolError("Command is empty")
    command_text = command or " ".join(argv)

    try:
        completed = subprocess.run(
            argv,
            cwd=context.resolved_workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"Command executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        output = combine_output(exc.stdout or "", exc.stderr or "")
        return truncate_output(output), {
            "command": command_text,
            "argv": argv,
            "exit_code": None,
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
        }

    output = combine_output(completed.stdout, completed.stderr)
    truncated_output = truncate_output(output)
    return truncated_output, {
        "command": command_text,
        "argv": argv,
        "exit_code": completed.returncode,
        "timed_out": False,
        "timeout_seconds": timeout_seconds,
        "truncated": len(output) > DEFAULT_MAX_OUTPUT_CHARS,
    }


def split_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError as exc:
        raise ToolError(f"Invalid command syntax: {exc}") from exc


def combine_output(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout.rstrip()}\n{stderr.rstrip()}"
    return stdout or stderr


def truncate_output(output: str) -> str:
    if len(output) <= DEFAULT_MAX_OUTPUT_CHARS:
        return output
    return output[:DEFAULT_MAX_OUTPUT_CHARS]
