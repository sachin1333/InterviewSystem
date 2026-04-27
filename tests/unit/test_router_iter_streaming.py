from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter


def test_iter_streaming_yields_chunks_in_order() -> None:
    fake = FakeRouter(scripted_streams=[["Hello, ", "this is ", "a probe."]])
    router = ModelRouter(fake)
    chunks = list(router.iter_streaming(tier="mid", prompt="any"))
    assert chunks == ["Hello, ", "this is ", "a probe."]


def test_iter_streaming_empty_returns_empty() -> None:
    fake = FakeRouter(scripted_streams=[[]])
    router = ModelRouter(fake)
    assert list(router.iter_streaming(tier="mid", prompt="any")) == []
