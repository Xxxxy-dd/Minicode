import json
import os
import urllib.error
import urllib.request

from minicode_agent.models.client import ModelClient, ModelMessage, ModelResponse


class OpenAICompatibleClient(ModelClient):
    """Minimal OpenAI-compatible chat completions adapter using the standard library."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 60,
        json_response_format: bool = True,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("MINICODE_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.json_response_format = json_response_format

    def complete(self, messages: list[ModelMessage]) -> ModelResponse:
        if not self.api_key:
            raise RuntimeError("Model API key is missing. Set MINICODE_MODEL_API_KEY or OPENAI_API_KEY.")

        body = {
            "model": self.model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": 0,
        }
        if self.json_response_format:
            body["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Model request failed: {exc.reason}") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("Model response did not include choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Model response did not include assistant content.")

        usage = payload.get("usage") or {}
        return ModelResponse(
            content=content,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            metadata={"provider": "openai-compatible", "model": self.model},
        )
