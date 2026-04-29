from __future__ import annotations

from collections.abc import Generator, Iterable

from adapters.llm.router import CHEAP_TIMEOUT_PLACEHOLDER, ModelRouter


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
