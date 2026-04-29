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
from urllib.error import HTTPError, URLError

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
        if stream:
            return self._stream_request(req, timeout=timeout)

        try:
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
                return body["choices"][0]["message"]["content"]  # type: ignore[no-any-return]
        except TimeoutError as exc:
            raise TimeoutError(f"openrouter timeout ({timeout}s)") from exc
        except HTTPError as exc:
            raise RuntimeError(self._format_http_error(exc)) from exc
        except URLError as exc:
            raise RuntimeError(f"openrouter error: {exc}") from exc

    def _stream_request(
        self, req: urllib_request.Request, *, timeout: float | None
    ) -> Generator[str, None, None]:
        try:
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                yield from self._stream_response(resp)
        except TimeoutError as exc:
            raise TimeoutError(f"openrouter timeout ({timeout}s)") from exc
        except HTTPError as exc:
            raise RuntimeError(self._format_http_error(exc)) from exc
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

    @staticmethod
    def _format_http_error(exc: HTTPError) -> str:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            raw = ""
        if raw:
            detail = OpenRouterRouter._extract_error_detail(raw)
        suffix = f": {detail}" if detail else ""
        return f"openrouter error {exc.code} {exc.reason}{suffix}"

    @staticmethod
    def _extract_error_detail(raw: str) -> str:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:500]

        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message:
                    return message[:500]
            message = parsed.get("message")
            if isinstance(message, str) and message:
                return message[:500]
        return raw[:500]
