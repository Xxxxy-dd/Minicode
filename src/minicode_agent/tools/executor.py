from typing import Any

from minicode_agent.permissions.policy import PermissionPolicy
from minicode_agent.tools.registry import ToolRegistry
from minicode_agent.tools.types import PermissionMode, ToolContext, ToolObservation


class ToolExecutor:
    """Executes tools through the permission gateway."""

    def __init__(self, registry: ToolRegistry, policy: PermissionPolicy | None = None) -> None:
        self.registry = registry
        self.policy = policy or PermissionPolicy()

    def execute(
        self,
        name: str,
        context: ToolContext,
        arguments: dict[str, Any] | None = None,
        approved: bool = False,
    ) -> ToolObservation:
        arguments = arguments or {}
        tool = self.registry.get(name)
        decision = self.policy.decide(
            tool.spec,
            arguments=arguments,
            workspace=context.resolved_workspace,
        )
        if decision.mode == PermissionMode.DENY:
            return ToolObservation(
                tool_call_id=f"{name}_permission_denied",
                ok=False,
                error=decision.reason,
                metadata={
                    "permission": decision.mode.value,
                    "permission_reason": decision.reason,
                    "tool": name,
                },
            )
        if decision.mode == PermissionMode.ASK and not approved:
            return ToolObservation(
                tool_call_id=f"{name}_approval_required",
                ok=False,
                error=decision.reason,
                metadata={
                    "permission": decision.mode.value,
                    "permission_reason": decision.reason,
                    "tool": name,
                },
            )

        observation = tool.run(context, arguments)
        observation.metadata.update(
            {
                "permission": PermissionMode.ALLOW.value if approved else decision.mode.value,
                "permission_reason": decision.reason,
                "approved": approved,
                "tool": name,
            }
        )
        return observation
