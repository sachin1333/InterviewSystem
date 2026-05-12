from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from core.observability import AuditLogger, MetricSink

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
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> RouterResponse: ...


class ModelRouter:
    """Retry/deadline wrapper for LLM providers.

    ``tier`` is now a legacy timeout label.  Production OpenAI calls use a
    single model regardless of tier.
    """

    def __init__(
        self,
        provider: RouterProvider,
        *,
        cheap_timeout: float = 2.5,
        mid_timeout: float = 6.0,
        top_timeout: float = 15.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        audit_logger: AuditLogger | None = None,
        metrics: MetricSink | None = None,
    ) -> None:
        self.provider = provider
        self.cheap_timeout = cheap_timeout
        self.mid_timeout = mid_timeout
        self.top_timeout = top_timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.audit_logger = audit_logger
        self.metrics = metrics

    def call(
        self,
        tier: Tier,
        prompt: str,
        stream: bool = False,
        deadline_ms: int | None = None,
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
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
                call_started = time.monotonic()
                response = self.provider.call(
                        **self._provider_call_kwargs(
                            tier=tier,
                            prompt=prompt,
                            stream=stream,
                            timeout=effective_timeout,
                            json_schema=json_schema,
                        schema_name=schema_name,
                        max_completion_tokens=max_completion_tokens,
                    )
                )
                if self.metrics is not None:
                    self.metrics.observe_llm_call(
                        component=f"router:{tier}",
                        elapsed_ms=(time.monotonic() - call_started) * 1000,
                        outcome="ok",
                    )
                if self.audit_logger is not None and isinstance(response, str):
                    self.audit_logger.record_llm_call(
                        agent_name=f"router:{tier}",
                        model_id="provider",
                        prompt=prompt,
                        response=response,
                    )
                return response
            except TimeoutError as exc:
                if self.metrics is not None:
                    self.metrics.observe_llm_call(
                        component=f"router:{tier}",
                        elapsed_ms=(time.monotonic() - call_started) * 1000,
                        outcome="timeout",
                    )
                last_exc = exc
                if deadline is None or (deadline - time.monotonic()) > 0:
                    fallback = self._call_fallback(
                        tier=tier,
                        prompt=prompt,
                        stream=stream,
                        deadline=deadline,
                        json_schema=json_schema,
                        schema_name=schema_name,
                        max_completion_tokens=max_completion_tokens,
                    )
                    if fallback is not None:
                        return fallback
            except Exception as exc:
                if self.metrics is not None:
                    self.metrics.observe_llm_call(
                        component=f"router:{tier}",
                        elapsed_ms=(time.monotonic() - call_started) * 1000,
                        outcome="error",
                    )
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
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> dict[str, Any]:
        if json_schema is not None:
            response = self.call(
                tier=tier,
                prompt=prompt,
                stream=False,
                deadline_ms=deadline_ms,
                json_schema=json_schema,
                schema_name=schema_name,
                max_completion_tokens=max_completion_tokens,
            )
            try:
                parsed = json.loads(self._response_text(response))
            except json.JSONDecodeError:
                if self.metrics is not None:
                    self.metrics.increment_structured_output_failure(
                        component=schema_name or "structured_call",
                        reason="json_decode",
                    )
                raise
            if not isinstance(parsed, dict):
                if self.metrics is not None:
                    self.metrics.increment_structured_output_failure(
                        component=schema_name or "structured_call",
                        reason="not_object",
                    )
                raise ValueError("model did not return a JSON object")
            self._raise_if_missing_required_keys(parsed, schema)
            return parsed

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

            try:
                self._raise_if_missing_required_keys(parsed, schema)
            except ValueError:
                if attempt == 0:
                    continue
                raise
            return parsed

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
        internally — it forwards each chunk so HTTP/SSE handlers can flush. If
        the upstream provider fails before or during iteration, degrade to the
        deterministic placeholder so SSE responses can close cleanly instead of
        raising after HTTP 200 has already been sent.
        """
        try:
            iterator = self.call(tier=tier, prompt=prompt, stream=True)
            if isinstance(iterator, str):
                if iterator:
                    yield iterator
                return
            for chunk in iterator:
                if chunk:
                    yield chunk
        except Exception:
            yield from self._placeholder_response(stream=True)

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
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> RouterResponse | None:
        """Timeout fallback chain: top -> mid -> cheap -> placeholder.

        Single-model providers ignore the tier for model selection; this only
        changes the timeout label passed through the compatibility interface.
        """
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
                    **self._provider_call_kwargs(
                        tier=fallback_tier,
                        prompt=prompt,
                        stream=stream,
                    timeout=fallback_timeout,
                    json_schema=json_schema,
                    schema_name=schema_name,
                    max_completion_tokens=max_completion_tokens,
                )
            )
        except TimeoutError:
            return self._call_fallback(
                tier=fallback_tier,
                prompt=prompt,
                stream=stream,
                deadline=deadline,
                json_schema=json_schema,
                schema_name=schema_name,
                max_completion_tokens=max_completion_tokens,
            )
        except Exception:
            return self._call_fallback(
                tier=fallback_tier,
                prompt=prompt,
                stream=stream,
                deadline=deadline,
                json_schema=json_schema,
                schema_name=schema_name,
                max_completion_tokens=max_completion_tokens,
            )

    def _placeholder_response(self, *, stream: bool) -> RouterResponse:
        if stream:
            return (chunk for chunk in (CHEAP_TIMEOUT_PLACEHOLDER,))
        return CHEAP_TIMEOUT_PLACEHOLDER

    def _provider_call_kwargs(
        self,
        *,
        tier: Tier,
        prompt: str,
        stream: bool,
        timeout: float | None,
        json_schema: dict[str, object] | None,
        schema_name: str | None,
        max_completion_tokens: int | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "tier": tier,
            "prompt": prompt,
            "stream": stream,
            "timeout": timeout,
        }
        if json_schema is not None and self._provider_accepts_structured_kwargs():
            kwargs["json_schema"] = json_schema
            kwargs["schema_name"] = schema_name
            kwargs["max_completion_tokens"] = max_completion_tokens
        return kwargs

    def _provider_accepts_structured_kwargs(self) -> bool:
        try:
            parameters = inspect.signature(self.provider.call).parameters
        except (TypeError, ValueError):
            return True
        return "json_schema" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    @staticmethod
    def _raise_if_missing_required_keys(
        parsed: dict[str, Any], schema: set[str] | Mapping[str, object]
    ) -> None:
        required_keys = schema if isinstance(schema, set) else set(schema)
        missing = required_keys - set(parsed)
        if missing:
            raise ValueError(f"model JSON missing required keys: {sorted(missing)}")

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
