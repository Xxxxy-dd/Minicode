import re
import time
from typing import Any

from minicode_agent.permissions.policy import PermissionPolicy
from minicode_agent.trace.store import TraceStore
from minicode_agent.tools.registry import ToolRegistry
from minicode_agent.tools.types import PermissionMode, ToolContext, ToolObservation


class ToolExecutor:
    """Executes tools through the permission gateway."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy | None = None,
        trace_store: TraceStore | None = None,
        run_id: str | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or PermissionPolicy()
        self.trace_store = trace_store
        self.run_id = run_id

    def execute(
        self,
        name: str,
        context: ToolContext,
        arguments: dict[str, Any] | None = None,
        approved: bool = False,
    ) -> ToolObservation:
        arguments = arguments or {}
        tool = self.registry.get(name)
        started_at = time.perf_counter()
        self._trace(
            "tool_requested",
            {
                "tool": name,
                "arguments": safe_payload(arguments),
                "approved": approved,
            },
        )
        decision = self.policy.decide(
            tool.spec,
            arguments=arguments,
            workspace=context.resolved_workspace,
        )
        self._trace(
            "permission_checked",
            {
                "tool": name,
                "decision": decision.mode.value,
                "reason": decision.reason,
            },
        )
        if decision.mode == PermissionMode.DENY:
            observation = ToolObservation(
                tool_call_id=f"{name}_permission_denied",
                ok=False,
                error=decision.reason,
                metadata={
                    "permission": decision.mode.value,
                    "permission_reason": decision.reason,
                    "tool": name,
                },
            )
            self._trace_observation(observation, started_at)
            return observation
        if decision.mode == PermissionMode.ASK and not approved:
            observation = ToolObservation(
                tool_call_id=f"{name}_approval_required",
                ok=False,
                error=decision.reason,
                metadata={
                    "permission": decision.mode.value,
                    "permission_reason": decision.reason,
                    "tool": name,
                },
            )
            self._trace_observation(observation, started_at)
            return observation

        observation = tool.run(context, arguments)
        observation.metadata.update(
            {
                "permission": PermissionMode.ALLOW.value if approved else decision.mode.value,
                "permission_reason": decision.reason,
                "approved": approved,
                "tool": name,
            }
        )
        self._trace_observation(observation, started_at)
        return observation

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.trace_store is None or self.run_id is None:
            return
        self.trace_store.append(self.run_id, event_type, payload)

    def _trace_observation(self, observation: ToolObservation, started_at: float) -> None:
        self._trace(
            "tool_finished",
            {
                "tool_call_id": observation.tool_call_id,
                "ok": observation.ok,
                "error": observation.error,
                "metadata": safe_payload(observation.metadata),
                "output_chars": len(observation.output),
                "truncated": observation.truncated,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
            },
        )


def safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            cleaned_value = redact_value(key, safe_payload(item))
            if cleaned_value is None or cleaned_value == {} or cleaned_value == []:
                continue
            cleaned[key] = cleaned_value
        return cleaned
    if isinstance(value, list):
        return [item for item in (safe_payload(item) for item in value) if item is not None]
    if isinstance(value, str):
        return redact_secret_patterns(value)
    return value


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("secret", "token", "password", "key")):
        return "[redacted]"
    return value


def redact_secret_patterns(value: str) -> str:
    patterns = (
        r"(?i)(authorization:\s*bearer\s+)[^\s]+",
        r"(?i)(api[_-]?key\s*=\s*)[^\s]+",
        r"(?i)(openai_api_key\s*=\s*)[^\s]+",
        r"(?i)(anthropic_api_key\s*=\s*)[^\s]+",
    )
    redacted = value
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[redacted]", redacted)
    return redacted
