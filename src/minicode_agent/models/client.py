from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


class ModelClient(Protocol):
    """Small interface that keeps model providers out of the agent core."""

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        """Return a single assistant response for a chat-style prompt."""
