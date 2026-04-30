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
