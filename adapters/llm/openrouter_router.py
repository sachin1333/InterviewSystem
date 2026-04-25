"""OpenRouter LLM provider.

Requires OPENROUTER_API_KEY env var.
Per-tier model selection via env:
  OPENROUTER_TOP_MODEL    (default: moonshotai/kimi-k2.6)
  OPENROUTER_MID_MODEL    (default: deepseek/deepseek-v3)
  OPENROUTER_CHEAP_MODEL  (default: meta-llama/llama-3.1-8b-instruct)
"""
from __future__ import annotations

import json
import os
from collections.abc import Generator, Iterable
from http.client import HTTPResponse
from typing import Literal
from urllib import request as urllib_request
from urllib.error import URLError

type Tier = Literal["cheap", "mid", "top"]

_DEFAULT_MODELS: dict[Tier, str] = {
    "cheap": "meta-llama/llama-3.1-8b-instruct",
    "mid":   "deepseek/deepseek-v3",
    "top":   "moonshotai/kimi-k2.6",
}


class OpenRouterRouter:
    """RouterProvider that calls openrouter.ai."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        top_model: str | None = None,
        mid_model: str | None = None,
        cheap_model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._models: dict[Tier, str] = {
            "top":   top_model   or os.environ.get("OPENROUTER_TOP_MODEL",   _DEFAULT_MODELS["top"]),
            "mid":   mid_model   or os.environ.get("OPENROUTER_MID_MODEL",   _DEFAULT_MODELS["mid"]),
            "cheap": cheap_model or os.environ.get("OPENROUTER_CHEAP_MODEL", _DEFAULT_MODELS["cheap"]),
        }

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str | Iterable[str]:
        # Cast tier to Tier to handle type narrowing
        model_tier: Tier = tier if tier in ("cheap", "mid", "top") else "mid"  # type: ignore[assignment]
        model = self._models.get(model_tier, _DEFAULT_MODELS["mid"])
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }).encode()

        req = urllib_request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                if stream:
                    return self._stream_response(resp)
                body = json.loads(resp.read())
                return body["choices"][0]["message"]["content"]  # type: ignore[no-any-return]
        except TimeoutError as exc:
            raise TimeoutError(f"openrouter timeout ({timeout}s)") from exc
        except URLError as exc:
            raise RuntimeError(f"openrouter error: {exc}") from exc

    def _stream_response(self, resp: HTTPResponse) -> Generator[str, None, None]:
        # Read SSE chunks and yield text deltas.
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0]["delta"].get("content", "")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError):
                continue
