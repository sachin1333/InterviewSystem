from __future__ import annotations

import contextlib
from collections.abc import Generator, Iterable

from adapters.llm.router import CHEAP_TIMEOUT_PLACEHOLDER, ModelRouter
from core.observability import MetricSink


class _AlwaysTimeoutProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float | None]] = []

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del prompt, stream
        self.calls.append((tier, timeout))
        raise TimeoutError(f"{tier} timed out")


class _TopTimeoutMidSuccessProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del prompt, stream, timeout
        self.calls.append(tier)
        if tier == "top":
            raise TimeoutError("top timed out")
        return '{"prompt_markdown":"fallback from mid","turn_kind":"question"}'


class _MalformedThenValidProvider:
    def __init__(self) -> None:
        self.calls = 0

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        self.calls += 1
        if self.calls == 1:
            return "not-json"
        return '{"prompt_markdown":"valid on retry","turn_kind":"question"}'


class _StaticProvider:
    def __init__(self, response: str) -> None:
        self.response = response

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
    ) -> str:
        del tier, prompt, stream, timeout, json_schema, schema_name, max_completion_tokens
        return self.response


def test_cheap_timeout_returns_deterministic_placeholder() -> None:
    provider = _AlwaysTimeoutProvider()
    router = ModelRouter(provider, max_retries=1, sleep=lambda _: None)

    response = router.call(tier="cheap", prompt="hello", stream=False)

    assert response == CHEAP_TIMEOUT_PLACEHOLDER
    assert provider.calls == [("cheap", 2.5)]


def test_top_timeout_falls_back_to_mid() -> None:
    provider = _TopTimeoutMidSuccessProvider()
    router = ModelRouter(provider, max_retries=1, sleep=lambda _: None)

    response = router.call(tier="top", prompt="hello", stream=False)

    assert response == '{"prompt_markdown":"fallback from mid","turn_kind":"question"}'
    assert provider.calls == ["top", "mid"]


def test_call_json_retries_after_malformed_json() -> None:
    provider = _MalformedThenValidProvider()
    router = ModelRouter(provider, max_retries=1, sleep=lambda _: None)

    response = router.call_json(
        tier="top",
        prompt="hello",
        schema={"prompt_markdown", "turn_kind"},
    )

    assert response == {
        "prompt_markdown": "valid on retry",
        "turn_kind": "question",
    }
    assert provider.calls == 2


def test_router_records_llm_and_structured_failure_metrics() -> None:
    metrics = MetricSink()
    provider = _MalformedThenValidProvider()
    router = ModelRouter(provider, max_retries=1, sleep=lambda _: None, metrics=metrics)

    response = router.call_json(
        tier="top",
        prompt="hello",
        schema={"prompt_markdown", "turn_kind"},
    )

    exported = metrics.export_prometheus()
    assert response["turn_kind"] == "question"
    assert 'llm_call_ms_count{component="router:top",outcome="ok"} 2' in exported


def test_structured_call_json_records_decode_failures() -> None:
    metrics = MetricSink()
    router = ModelRouter(
        _StaticProvider("wrapped {'not':'direct-json'}"),
        max_retries=1,
        sleep=lambda _: None,
        metrics=metrics,
    )

    with contextlib.suppress(Exception):
        router.call_json(
            tier="mid",
            prompt="hello",
            schema={"signals"},
            json_schema={"type": "object"},
            schema_name="problem_scoring_result",
        )

    assert (
        'structured_output_failures_total{component="problem_scoring_result",reason="json_decode"} 1'
        in metrics.export_prometheus()
    )


class _AlwaysFailProvider:
    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        raise RuntimeError("provider rejected request")


class _StreamingIteratorFailsProvider:
    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> Iterable[str]:
        del tier, prompt, stream, timeout

        def _chunks() -> Generator[str, None, None]:
            raise RuntimeError("stream broke")
            yield "unreachable"

        return _chunks()


def test_iter_streaming_returns_placeholder_when_provider_call_fails() -> None:
    router = ModelRouter(_AlwaysFailProvider(), max_retries=1, sleep=lambda _: None)

    chunks = list(router.iter_streaming(tier="mid", prompt="hello"))

    assert chunks == [CHEAP_TIMEOUT_PLACEHOLDER]


def test_iter_streaming_returns_placeholder_when_provider_iterator_fails() -> None:
    router = ModelRouter(_StreamingIteratorFailsProvider(), max_retries=1, sleep=lambda _: None)

    chunks = list(router.iter_streaming(tier="mid", prompt="hello"))

    assert chunks == [CHEAP_TIMEOUT_PLACEHOLDER]
