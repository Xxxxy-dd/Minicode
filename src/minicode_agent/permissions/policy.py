from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel

from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolSpec


class PermissionDecision(BaseModel):
    mode: PermissionMode
    reason: str


class PathSandbox:
    """Validates tool path arguments against a workspace root."""

    path_argument_names = ("path", "target_path", "file_path")

    def __init__(self, workspace: Path, path_argument_names: tuple[str, ...] | None = None) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.path_argument_names = path_argument_names or self.path_argument_names

    def validate_arguments(self, arguments: dict[str, Any]) -> PermissionDecision | None:
        for key in self.path_argument_names:
            value = arguments.get(key)
            if value is None:
                continue
            decision = self.validate_path(str(value))
            if decision.mode == PermissionMode.DENY:
                return decision
        return None

    def validate_path(self, requested_path: str) -> PermissionDecision:
        target = (self.workspace / requested_path).expanduser().resolve()
        if target != self.workspace and self.workspace not in target.parents:
            return PermissionDecision(
                mode=PermissionMode.DENY,
                reason=f"Path escapes workspace: {requested_path}",
            )
        return PermissionDecision(mode=PermissionMode.ALLOW, reason="Path is inside workspace.")


class SensitivePathPolicy:
    """Blocks paths that are likely to contain credentials or local machine secrets."""

    sensitive_names = {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".netrc",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "known_hosts",
        "authorized_keys",
    }
    sensitive_dirs = {".ssh", ".gnupg"}
    sensitive_suffixes = {".pem", ".key", ".p12", ".pfx"}

    def validate_arguments(self, arguments: dict[str, Any], path_argument_names: tuple[str, ...]) -> PermissionDecision | None:
        for key in path_argument_names:
            value = arguments.get(key)
            if value is None:
                continue
            decision = self.validate_path(str(value))
            if decision.mode == PermissionMode.DENY:
                return decision
        return None

    def validate_path(self, requested_path: str) -> PermissionDecision:
        if is_sensitive_path(requested_path):
            return PermissionDecision(
                mode=PermissionMode.DENY,
                reason=f"Sensitive path is blocked by policy: {requested_path}",
            )
        return PermissionDecision(mode=PermissionMode.ALLOW, reason="Path is not sensitive.")


class CommandSafetyClassifier:
    """Classifies shell commands that should never be executed by the agent."""

    blocked_patterns = (
        r"\brm\s+-rf\b",
        r"\bdel\s+/[sq]\b",
        r"\berase\s+/[sq]\b",
        r"\brd\s+/s\b",
        r"\brmdir\s+/s\b",
        r"\bremove-item\b.*-recurse\b",
        r"\bremove-item\b.*-force\b",
        r"\bformat\b",
        r"\bdiskpart\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\brestart-computer\b",
        r"\bstop-computer\b",
        r"\bset-executionpolicy\b",
        r"\breg\s+delete\b",
        r"\bgit\s+push\b",
        r"\bcurl\b.*\|",
        r"\bwget\b.*\|",
        r"\binvoke-webrequest\b.*\|",
        r"\binvoke-restmethod\b.*\|",
        r"\biwr\b.*\|",
        r"\birm\b.*\|",
        r"\.env\b.*\b(curl|wget|scp|sftp)\b",
    )

    def classify(self, command: str) -> PermissionDecision:
        normalized = command.strip().lower()
        for pattern in self.blocked_patterns:
            if re.search(pattern, normalized):
                return PermissionDecision(
                    mode=PermissionMode.DENY,
                    reason=f"Command blocked by safety policy: {pattern}",
                )
        return PermissionDecision(mode=PermissionMode.ALLOW, reason="Command passed safety classifier.")


class PermissionPolicy:
    """First-pass deterministic policy for tool permissions and risk levels."""

    def decide(self, tool: ToolSpec, arguments: dict[str, Any] | None = None, workspace: Path | None = None) -> PermissionDecision:
        arguments = arguments or {}
        if workspace is not None:
            path_arg_names = tool.path_arg_names or PathSandbox.path_argument_names
            sandbox_decision = PathSandbox(workspace, path_arg_names).validate_arguments(arguments)
            if sandbox_decision is not None:
                return sandbox_decision
            sensitive_path_decision = SensitivePathPolicy().validate_arguments(arguments, path_arg_names)
            if sensitive_path_decision is not None:
                return sensitive_path_decision
        command_text = command_text_from_arguments(arguments, tool.command_arg_names)
        if command_text is not None:
            command_decision = CommandSafetyClassifier().classify(command_text)
            if command_decision.mode == PermissionMode.DENY:
                return command_decision
        if tool.permission == PermissionMode.DENY:
            return PermissionDecision(mode=PermissionMode.DENY, reason="Tool is denied by its permission mode.")
        if tool.permission == PermissionMode.ASK:
            return PermissionDecision(mode=PermissionMode.ASK, reason="Tool requires approval by its permission mode.")
        if tool.risk_level == RiskLevel.BLOCKED:
            return PermissionDecision(mode=PermissionMode.DENY, reason="Tool is blocked by policy.")
        if tool.risk_level in {RiskLevel.HIGH, RiskLevel.MEDIUM}:
            return PermissionDecision(mode=PermissionMode.ASK, reason="Tool requires approval.")
        return PermissionDecision(mode=PermissionMode.ALLOW, reason="Tool is allowed by default.")


def command_text_from_arguments(arguments: dict[str, Any], command_arg_names: tuple[str, ...] = ()) -> str | None:
    command_names = command_arg_names or ("command",)
    argv = arguments.get("argv") if "argv" in command_names else None
    if isinstance(argv, list):
        return " ".join(str(part) for part in argv)
    command = next((arguments.get(name) for name in command_names if name != "argv" and arguments.get(name) is not None), None)
    if command is not None:
        return str(command)
    return None


def is_sensitive_path(requested_path: str) -> bool:
    path = Path(requested_path)
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    suffix = path.suffix.lower()
    return (
        bool(parts & SensitivePathPolicy.sensitive_dirs)
        or name in SensitivePathPolicy.sensitive_names
        or suffix in SensitivePathPolicy.sensitive_suffixes
    )
