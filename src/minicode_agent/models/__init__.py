"""Model adapters and prompt helpers."""

from minicode_agent.models.client import ModelClient, ModelMessage, ModelResponse
from minicode_agent.models.openai_compatible import OpenAICompatibleClient
from minicode_agent.models.parser import ModelAction, ModelPlan, parse_model_plan
from minicode_agent.models.prompts import build_planning_prompt

__all__ = [
    "ModelAction",
    "ModelClient",
    "ModelMessage",
    "ModelPlan",
    "ModelResponse",
    "OpenAICompatibleClient",
    "build_planning_prompt",
    "parse_model_plan",
]
