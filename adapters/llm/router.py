from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

type Tier = Literal["cheap", "mid", "top"]
type RouterResponse = str | Iterable[str]


@dataclass(frozen=True)
class StreamingMetrics:
    text: str
    ttft_ms: int
    total_ms: int


CHEAP_TIMEOUT_PLACEHOLDER = (
    '{"prompt_markdown":"Fallback prompt: explain your approach, assumptions, and trade-offs.",'
    '"turn_kind":"question"}'
)


class RouterProvider(Protocol):
    def call(
        self,
        *,
        tier: Tier,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> RouterResponse: ...


class ModelRouter:
    """Retrying router wrapper with timeout fallback helpers."""

    def __init__(
        self,
        provider: RouterProvider,
        *,
        cheap_timeout: float = 2.5,
        mid_timeout: float = 6.0,
        top_timeout: float = 15.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.cheap_timeout = cheap_timeout
        self.mid_timeout = mid_timeout
        self.top_timeout = top_timeout
        self.max_retries = max_retries
        self.sleep = sleep

    def call(
        self,
        tier: Tier,
        prompt: str,
        stream: bool = False,
        deadline_ms: int | None = None,
    ) -> RouterResponse:
        tier_timeout = self._get_timeout(tier)
        deadline = (
            time.monotonic() + deadline_ms / 1000.0 if deadline_ms is not None else None
        )
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            remaining = (deadline - time.monotonic()) if deadline is not None else None
            if remaining is not None and remaining <= 0:
                last_exc = last_exc or TimeoutError("deadline exceeded")
                break
            effective_timeout = (
                min(remaining, tier_timeout) if remaining is not None else tier_timeout
            )
            try:
                return self.provider.call(
                    tier=tier,
                    prompt=prompt,
                    stream=stream,
                    timeout=effective_timeout,
                )
            except TimeoutError as exc:
                last_exc = exc
                if deadline is None or (deadline - time.monotonic()) > 0:
                    fallback = self._call_fallback(
                        tier=tier, prompt=prompt, stream=stream, deadline=deadline,
                    )
                    if fallback is not None:
                        return fallback
            except Exception as exc:
                last_exc = exc

            if attempt < self.max_retries:
                if deadline is not None and (deadline - time.monotonic()) <= 0:
                    break
                self.sleep(0.1 * (2 ** (attempt - 1)))

        if isinstance(last_exc, TimeoutError):
            return self._placeholder_response(stream=stream)
        raise RuntimeError("model router failed") from last_exc

    def call_json(
        self,
        *,
        tier: Tier,
        prompt: str,
        schema: set[str] | Mapping[str, object],
        deadline_ms: int | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            response = self.call(
                tier=tier, prompt=prompt, stream=False, deadline_ms=deadline_ms
            )
            text = self._response_text(response)
            try:
                parsed = json.loads(_extract_json(text))
            except json.JSONDecodeError:
                if attempt == 0:
                    continue
                raise

            if not isinstance(parsed, dict):
                if attempt == 0:
                    continue
                raise ValueError("model did not return a JSON object")

            required_keys = schema if isinstance(schema, set) else set(schema)
            missing = required_keys - set(parsed)
            if not missing:
                return parsed

            if attempt == 0:
                continue
            raise ValueError(f"model JSON missing required keys: {sorted(missing)}")

        raise AssertionError("unreachable")

    def call_streaming_with_metrics(
        self, *, tier: Tier, prompt: str
    ) -> StreamingMetrics:
        """Call the provider in streaming mode and capture TTFT and total latency."""
        start = time.monotonic()
        iterator = self.call(tier=tier, prompt=prompt, stream=True)

        # Handle case where provider returned a single string despite stream=True
        if isinstance(iterator, str):
            total_ms = int((time.monotonic() - start) * 1000)
            return StreamingMetrics(text=iterator, ttft_ms=0, total_ms=total_ms)

        it = iter(iterator)
        first = next(it, None)

        # Handle empty response
        if first is None or first == "":
            return StreamingMetrics(text="", ttft_ms=0, total_ms=0)

        ttft_ms = int((time.monotonic() - start) * 1000)
        rest = "".join(it)
        total_ms = int((time.monotonic() - start) * 1000)

        return StreamingMetrics(text=first + rest, ttft_ms=ttft_ms, total_ms=total_ms)

    def iter_streaming(self, *, tier: Tier, prompt: str) -> Iterator[str]:
        """Yield token chunks as they arrive from the provider.

        Unlike call_streaming_with_metrics this does not consume the iterator
        internally — it forwards each chunk so HTTP/SSE handlers can flush.
        """
        iterator = self.call(tier=tier, prompt=prompt, stream=True)
        if isinstance(iterator, str):
            if iterator:
                yield iterator
            return
        for chunk in iterator:
            if chunk:
                yield chunk

    def _get_timeout(self, tier: Tier) -> float:
        return {
            "cheap": self.cheap_timeout,
            "mid": self.mid_timeout,
            "top": self.top_timeout,
        }[tier]

    def _call_fallback(
        self,
        *,
        tier: Tier,
        prompt: str,
        stream: bool,
        deadline: float | None = None,
    ) -> RouterResponse | None:
        """Fallback chain: top -> mid -> cheap -> placeholder."""
        fallback_tier: Tier | None = None
        if tier == "top":
            fallback_tier = "mid"
        elif tier == "mid":
            fallback_tier = "cheap"

        if fallback_tier is None:
            return self._placeholder_response(stream=stream)

        if deadline is not None and (deadline - time.monotonic()) <= 0:
            return self._placeholder_response(stream=stream)

        try:
            fallback_timeout = self._get_timeout(fallback_tier)
            if deadline is not None:
                fallback_timeout = min(fallback_timeout, deadline - time.monotonic())
            return self.provider.call(
                tier=fallback_tier,
                prompt=prompt,
                stream=stream,
                timeout=fallback_timeout,
            )
        except TimeoutError:
            return self._call_fallback(
                tier=fallback_tier, prompt=prompt, stream=stream, deadline=deadline,
            )
        except Exception:
            return self._call_fallback(
                tier=fallback_tier, prompt=prompt, stream=stream, deadline=deadline,
            )

    def _placeholder_response(self, *, stream: bool) -> RouterResponse:
        if stream:
            return (chunk for chunk in (CHEAP_TIMEOUT_PLACEHOLDER,))
        return CHEAP_TIMEOUT_PLACEHOLDER

    @staticmethod
    def _response_text(response: RouterResponse) -> str:
        if isinstance(response, str):
            return response
        return "".join(response)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> str:
    """Pull a JSON object out of model output that may wrap it in prose or fences."""
    text = text.strip()
    if not text:
        return text
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    # Fallback: locate the first {...} substring with balanced braces.
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]
