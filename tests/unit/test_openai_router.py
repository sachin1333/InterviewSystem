from __future__ import annotations

import json
from typing import Any

import pytest

from adapters.llm.openai_router import OpenAIRouter


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.status = 200
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


def test_openai_router_uses_single_gpt55_fast_model_for_all_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
        del timeout
        payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("adapters.llm.openai_router.urllib_request.urlopen", fake_urlopen)
    router = OpenAIRouter(api_key="test-key")

    assert router.call(tier="cheap", prompt="first") == "ok"
    assert router.call(tier="mid", prompt="second") == "ok"
    assert router.call(tier="top", prompt="third") == "ok"

    assert [payload["model"] for payload in payloads] == ["gpt-5.5", "gpt-5.5", "gpt-5.5"]
    assert [payload["reasoning_effort"] for payload in payloads] == ["low", "low", "low"]


def test_openai_router_allows_one_explicit_model_override_not_per_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
        del timeout
        payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("adapters.llm.openai_router.urllib_request.urlopen", fake_urlopen)
    monkeypatch.setenv("OPENAI_TOP_MODEL", "must-not-be-used")
    monkeypatch.setenv("OPENAI_MID_MODEL", "must-not-be-used")
    monkeypatch.setenv("OPENAI_CHEAP_MODEL", "must-not-be-used")
    router = OpenAIRouter(api_key="test-key", model="gpt-test-single")

    assert router.call(tier="top", prompt="hello") == "ok"

    assert payloads[0]["model"] == "gpt-test-single"


def _payload_from_request(request: Any) -> dict[str, Any]:
    return json.loads(request.data.decode("utf-8"))


def test_openai_router_structured_request_includes_response_format_and_token_limit() -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    router = OpenAIRouter(api_key="test-key", model="gpt-5")

    request = router._build_request(
        prompt="return json",
        stream=False,
        json_schema=schema,
        schema_name="structured_result",
        max_completion_tokens=500,
    )

    payload = _payload_from_request(request)
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_result",
            "strict": True,
            "schema": schema,
        },
    }
    assert payload["max_completion_tokens"] == 500


def test_openai_router_legacy_request_omits_structured_output_fields() -> None:
    router = OpenAIRouter(api_key="test-key", model="gpt-5")

    request = router._build_request(prompt="hello", stream=False)

    payload = _payload_from_request(request)
    assert "response_format" not in payload
    assert "max_completion_tokens" not in payload


def test_openai_router_only_sends_reasoning_effort_for_supported_reasoning_models() -> None:
    reasoning_router = OpenAIRouter(api_key="test-key", model="o3-mini", reasoning_effort="low")
    non_reasoning_router = OpenAIRouter(api_key="test-key", model="gpt-4.1", reasoning_effort="low")

    reasoning_payload = _payload_from_request(
        reasoning_router._build_request(prompt="hello", stream=False)
    )
    non_reasoning_payload = _payload_from_request(
        non_reasoning_router._build_request(prompt="hello", stream=False)
    )

    assert reasoning_payload["reasoning_effort"] == "low"
    assert "reasoning_effort" not in non_reasoning_payload
