from pydantic import BaseModel

from minicode_agent.tools.types import PermissionMode, RiskLevel, ToolSpec


class PermissionDecision(BaseModel):
    mode: PermissionMode
    reason: str


class PermissionPolicy:
    """First-pass deterministic policy for tool risk levels."""

    def decide(self, tool: ToolSpec) -> PermissionDecision:
        if tool.risk_level == RiskLevel.BLOCKED:
            return PermissionDecision(mode=PermissionMode.DENY, reason="Tool is blocked by policy.")
        if tool.risk_level in {RiskLevel.HIGH, RiskLevel.MEDIUM}:
            return PermissionDecision(mode=PermissionMode.ASK, reason="Tool requires approval.")
        return PermissionDecision(mode=PermissionMode.ALLOW, reason="Tool is allowed by default.")
