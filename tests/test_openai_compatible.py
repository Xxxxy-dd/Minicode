import json
import urllib.error

import pytest

from minicode_agent.models import ModelMessage, OpenAICompatibleClient


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_openai_client_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("MINICODE_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = OpenAICompatibleClient(model="demo", api_key=None)

    with pytest.raises(RuntimeError, match="API key is missing"):
        client.complete([ModelMessage(role="user", content="hello")])


def test_openai_client_parses_chat_completion(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHTTPResponse(
            {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(
        model="demo-model",
        api_key="secret",
        base_url="https://example.test/v1/",
        timeout_seconds=3,
    )

    response = client.complete([ModelMessage(role="user", content="hello")])

    assert response.content == '{"ok": true}'
    assert response.input_tokens == 5
    assert response.output_tokens == 7
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["timeout"] == 3
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "demo-model"


def test_openai_client_wraps_http_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=FakeHTTPResponse({"error": "bad key"}),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleClient(model="demo", api_key="secret")

    with pytest.raises(RuntimeError, match="HTTP 401"):
        client.complete([ModelMessage(role="user", content="hello")])
