from typing import Any

from minicode_agent.tools.base import BaseTool, ToolError
from minicode_agent.tools.shell import run_command
from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolContext, ToolIntent, ToolSpec


class RunFormatterTool(BaseTool):
    spec = ToolSpec(
        name="run_formatter",
        description="Run an explicitly provided formatter command in the workspace. Requires approval.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Formatter command string."},
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Formatter command argv. Preferred for exact argument boundaries.",
                },
                "timeout_seconds": {"type": "integer", "default": 60, "minimum": 1},
            },
            "anyOf": [{"required": ["command"]}, {"required": ["argv"]}],
        },
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        intents=(ToolIntent.COMMAND_RUN,),
        command_arg_names=("command", "argv"),
        capability="format_command",
        timeout_seconds=60,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        command = arguments.get("command")
        argv = arguments.get("argv")
        if not command and not argv:
            raise ToolError("Missing required argument: command")
        timeout = int(arguments.get("timeout_seconds") or self.spec.timeout_seconds)
        output, metadata = run_command(str(command) if command else None, context, timeout, argv=argv)
        metadata["quality_tool"] = "formatter"
        return output, metadata


class RunLinterTool(BaseTool):
    spec = ToolSpec(
        name="run_linter",
        description="Run an explicitly provided linter command in the workspace. Requires approval.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Linter command string."},
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Linter command argv. Preferred for exact argument boundaries.",
                },
                "timeout_seconds": {"type": "integer", "default": 60, "minimum": 1},
            },
            "anyOf": [{"required": ["command"]}, {"required": ["argv"]}],
        },
        risk_level=RiskLevel.MEDIUM,
        permission=PermissionMode.ASK,
        intents=(ToolIntent.COMMAND_RUN,),
        command_arg_names=("command", "argv"),
        capability="lint_command",
        timeout_seconds=60,
    )

    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        command = arguments.get("command")
        argv = arguments.get("argv")
        if not command and not argv:
            raise ToolError("Missing required argument: command")
        timeout = int(arguments.get("timeout_seconds") or self.spec.timeout_seconds)
        output, metadata = run_command(str(command) if command else None, context, timeout, argv=argv)
        metadata["quality_tool"] = "linter"
        return output, metadata
