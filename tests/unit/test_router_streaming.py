from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter, StreamingMetrics


def test_fake_router_streams_token_chunks() -> None:
    prompt = "say hi"
    fake = FakeRouter(
        scripted_responses={FakeRouter.prompt_hash(prompt): ["Hello voice world"]}
    )
    router = ModelRouter(fake)
    iterator = router.call(tier="mid", prompt=prompt, stream=True)
    chunks = list(iterator)
    assert "".join(chunks) == "Hello voice world"
    assert len(chunks) > 1, "stream should produce multiple chunks"


def test_streaming_records_first_token_latency() -> None:
    prompt = "x"
    fake = FakeRouter(
        scripted_responses={FakeRouter.prompt_hash(prompt): ["abc def ghi"]}
    )
    router = ModelRouter(fake)
    metrics = router.call_streaming_with_metrics(tier="mid", prompt=prompt)
    assert isinstance(metrics, StreamingMetrics)
    assert metrics.text == "abc def ghi"
    assert metrics.ttft_ms >= 0
    assert metrics.total_ms >= metrics.ttft_ms


def test_streaming_metrics_for_empty_response() -> None:
    prompt = "empty"
    fake = FakeRouter(
        scripted_responses={FakeRouter.prompt_hash(prompt): [""]}
    )
    router = ModelRouter(fake)
    metrics = router.call_streaming_with_metrics(tier="cheap", prompt=prompt)
    assert metrics.text == ""
    assert metrics.ttft_ms == 0
