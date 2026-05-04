from __future__ import annotations

import json
import os
import time
from collections.abc import Generator, Iterator
from http.client import HTTPResponse
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from adapters.llm.structured_schema import response_format

DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_OPENAI_REASONING_EFFORT = "low"
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


class OpenAIRouter:
    """OpenAI REST API provider using one fast model for every LLM task."""

    base_url = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self.reasoning_effort = (
            reasoning_effort
            or os.environ.get("OPENAI_REASONING_EFFORT")
            or DEFAULT_OPENAI_REASONING_EFFORT
        )
        self.max_retries = max_retries

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> str | Iterator[str]:
        del tier  # All tasks use the same OpenAI model; tiers no longer select models.
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set; cannot call OpenAI")

        req = self._build_request(
            prompt=prompt,
            stream=stream,
            json_schema=json_schema,
            schema_name=schema_name,
            max_completion_tokens=max_completion_tokens,
        )
        if stream:
            return self._stream_request(req, timeout=timeout)

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib_request.urlopen(req, timeout=timeout) as response:
                    body = json.loads(response.read())
                return self._extract_content(body)
            except TimeoutError:
                last_exc = TimeoutError(f"openai timeout ({timeout}s)")
                time.sleep(0.5 * attempt)
            except HTTPError as exc:
                formatted = self._format_http_error(exc)
                if exc.code != 429 and exc.code < 500:
                    raise RuntimeError(formatted) from exc
                last_exc = RuntimeError(formatted)
                time.sleep(0.2 * attempt)
            except URLError as exc:
                last_exc = self._format_url_error(exc)
                time.sleep(0.2 * attempt)

        if last_exc is not None:
            raise RuntimeError("OpenAI call failed") from last_exc
        raise RuntimeError("OpenAI call failed")

    def _build_request(
        self,
        *,
        prompt: str,
        stream: bool,
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> urllib_request.Request:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
        }
        if self._supports_reasoning_effort():
            payload["reasoning_effort"] = self.reasoning_effort
        if stream:
            payload["stream"] = True
        if json_schema is not None:
            payload["response_format"] = response_format(
                schema_name or "structured_response", json_schema
            )
        if max_completion_tokens is not None:
            payload["max_completion_tokens"] = max_completion_tokens

        return urllib_request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def _supports_reasoning_effort(self) -> bool:
        return self.model.lower().startswith(_REASONING_MODEL_PREFIXES)

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

    def _stream_request(
        self, req: urllib_request.Request, *, timeout: float | None
    ) -> Generator[str, None, None]:
        try:
            with urllib_request.urlopen(req, timeout=timeout) as response:
                yield from self._iter_stream(response)
        except TimeoutError as exc:
            raise TimeoutError(f"openai timeout ({timeout}s)") from exc
        except HTTPError as exc:
            raise RuntimeError(self._format_http_error(exc)) from exc
        except URLError as exc:
            raise RuntimeError(str(self._format_url_error(exc))) from exc

    def _iter_stream(self, response: HTTPResponse) -> Iterator[str]:
        for raw_line in response:
            if not raw_line:
                continue
            text = raw_line.decode("utf-8", errors="replace").strip()
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

    @staticmethod
    def _format_http_error(exc: HTTPError) -> str:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            raw = ""
        if raw:
            detail = OpenAIRouter._extract_error_detail(raw)
        suffix = f": {detail}" if detail else ""
        return f"openai error {exc.code} {exc.reason}{suffix}"

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

    @staticmethod
    def _format_url_error(exc: URLError) -> Exception:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return TimeoutError(str(reason))
        return RuntimeError(f"openai error: {reason}")
