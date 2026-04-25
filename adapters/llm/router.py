from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal, Protocol

type Tier = Literal["cheap", "mid", "top"]
type RouterResponse = str | Iterable[str]

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
        effective_timeout = (
            min(deadline_ms / 1000.0, tier_timeout)
            if deadline_ms is not None
            else tier_timeout
        )
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return self.provider.call(
                    tier=tier,
                    prompt=prompt,
                    stream=stream,
                    timeout=effective_timeout,
                )
            except TimeoutError as exc:
                last_exc = exc
                fallback = self._call_fallback(tier=tier, prompt=prompt, stream=stream)
                if fallback is not None:
                    return fallback
            except Exception as exc:
                last_exc = exc

            if attempt < self.max_retries:
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
                parsed = json.loads(text)
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

    def _get_timeout(self, tier: Tier) -> float:
        return {
            "cheap": self.cheap_timeout,
            "mid": self.mid_timeout,
            "top": self.top_timeout,
        }[tier]

    def _call_fallback(
        self, *, tier: Tier, prompt: str, stream: bool
    ) -> RouterResponse | None:
        """Fallback chain: top -> mid -> cheap -> placeholder."""
        fallback_tier: Tier | None = None
        if tier == "top":
            fallback_tier = "mid"
        elif tier == "mid":
            fallback_tier = "cheap"

        if fallback_tier is None:
            return self._placeholder_response(stream=stream)

        try:
            fallback_timeout = self._get_timeout(fallback_tier)
            return self.provider.call(
                tier=fallback_tier,
                prompt=prompt,
                stream=stream,
                timeout=fallback_timeout,
            )
        except TimeoutError:
            return self._call_fallback(
                tier=fallback_tier, prompt=prompt, stream=stream
            )
        except Exception:
            return self._call_fallback(
                tier=fallback_tier, prompt=prompt, stream=stream
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
