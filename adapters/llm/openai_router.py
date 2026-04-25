from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any


class OpenAIRouter:
    """OpenAI REST API router with small retry logic."""

    base_url = "https://api.openai.com/v1/chat/completions"

    def __init__(self, *, api_key: str | None = None, max_retries: int = 3) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.max_retries = max_retries

    def _model_for_tier(self, tier: str) -> str:
        tier_map = {
            "cheap": os.environ.get("OPENAI_CHEAP_MODEL", "gpt-4o-mini"),
            "mid": os.environ.get("OPENAI_MID_MODEL", "gpt-4-turbo"),
            "top": os.environ.get("OPENAI_TOP_MODEL", "gpt-4o"),
        }
        # Support legacy tier names
        legacy_map = {"fast": "cheap", "strong": "top"}
        normalized_tier = legacy_map.get(tier, tier)
        return tier_map.get(normalized_tier, "gpt-4o-mini")

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str | Iterator[str]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set; cannot call OpenAI")

        try:
            import requests  # type: ignore[import-untyped]
        except Exception as exc:  # pragma: no cover - dependency/environment dependent
            raise RuntimeError("requests package is required for OpenAIRouter") from exc

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model_for_tier(tier),
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if stream:
                    response = requests.post(
                        self.base_url,
                        headers=headers,
                        json={**payload, "stream": True},
                        timeout=timeout,
                        stream=True,
                    )
                    self._raise_for_status(response)
                    return self._iter_stream(response)

                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                self._raise_for_status(response)
                body = response.json()
                return self._extract_content(body)
            except requests.Timeout as exc:
                last_exc = exc
                time.sleep(0.5 * attempt)
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(0.2 * attempt)

        if last_exc is not None:
            raise RuntimeError("OpenAI call failed") from last_exc
        raise RuntimeError("OpenAI call failed")

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        if response.status_code == 429:
            raise RuntimeError("rate_limit")
        if response.status_code >= 500:
            raise RuntimeError("5xx")
        response.raise_for_status()

    @staticmethod
    def _extract_content(body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        texts: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
        return "".join(texts)

    def _iter_stream(self, response: Any) -> Iterator[str]:
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            text = line.decode() if isinstance(line, bytes) else line
            if text.startswith("data: "):
                text = text[len("data: ") :]
            if text.strip() == "[DONE]":
                break
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                yield text
                continue
            choices = parsed.get("choices") or []
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, str):
                    yield content
