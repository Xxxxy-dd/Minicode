from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

from minicode_agent.tools.types import ToolContext, ToolObservation, ToolSpec


class ToolError(Exception):
    """Expected tool execution error shown to the agent as an observation."""


class BaseTool(ABC):
    spec: ToolSpec

    def run(self, context: ToolContext, arguments: dict[str, Any] | None = None) -> ToolObservation:
        tool_call_id = f"{self.spec.name}_{uuid4().hex[:8]}"
        try:
            output, metadata = self._run(context, arguments or {})
            return ToolObservation(
                tool_call_id=tool_call_id,
                ok=True,
                output=output,
                metadata=metadata,
            )
        except ToolError as exc:
            return ToolObservation(tool_call_id=tool_call_id, ok=False, error=str(exc))
        except Exception as exc:  # pragma: no cover - defensive boundary
            return ToolObservation(
                tool_call_id=tool_call_id,
                ok=False,
                error=f"Unexpected tool error: {type(exc).__name__}: {exc}",
            )

    @abstractmethod
    def _run(self, context: ToolContext, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Execute tool and return output plus structured metadata."""
