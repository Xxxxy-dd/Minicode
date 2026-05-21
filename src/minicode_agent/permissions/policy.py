from pathlib import Path
from typing import Any

from pydantic import BaseModel

from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolSpec


class PermissionDecision(BaseModel):
    mode: PermissionMode
    reason: str


class PathSandbox:
    """Validates tool path arguments against a workspace root."""

    path_argument_names = ("path", "target_path", "file_path")

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.expanduser().resolve()

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


class PermissionPolicy:
    """First-pass deterministic policy for tool permissions and risk levels."""

    def decide(self, tool: ToolSpec, arguments: dict[str, Any] | None = None, workspace: Path | None = None) -> PermissionDecision:
        arguments = arguments or {}
        if workspace is not None:
            sandbox_decision = PathSandbox(workspace).validate_arguments(arguments)
            if sandbox_decision is not None:
                return sandbox_decision
        if tool.permission == PermissionMode.DENY:
            return PermissionDecision(mode=PermissionMode.DENY, reason="Tool is denied by its permission mode.")
        if tool.permission == PermissionMode.ASK:
            return PermissionDecision(mode=PermissionMode.ASK, reason="Tool requires approval by its permission mode.")
        if tool.risk_level == RiskLevel.BLOCKED:
            return PermissionDecision(mode=PermissionMode.DENY, reason="Tool is blocked by policy.")
        if tool.risk_level in {RiskLevel.HIGH, RiskLevel.MEDIUM}:
            return PermissionDecision(mode=PermissionMode.ASK, reason="Tool requires approval.")
        return PermissionDecision(mode=PermissionMode.ALLOW, reason="Tool is allowed by default.")
