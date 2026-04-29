from __future__ import annotations

from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from adapters.llm.openrouter_router import OpenRouterRouter


def test_http_error_includes_openrouter_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise HTTPError(
            url="https://openrouter.ai/api/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=Message(),
            fp=BytesIO(b'{"error":{"message":"No endpoints found for model moonshotai/kimi-k2.6"}}'),
        )

    monkeypatch.setattr("adapters.llm.openrouter_router.urllib_request.urlopen", fail_urlopen)
    router = OpenRouterRouter(api_key="test-key")

    with pytest.raises(RuntimeError) as exc_info:
        router.call(tier="top", prompt="hello")

    message = str(exc_info.value)
    assert "openrouter error 400 Bad Request" in message
    assert "No endpoints found for model moonshotai/kimi-k2.6" in message
    assert "test-key" not in message
