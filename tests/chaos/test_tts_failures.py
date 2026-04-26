import asyncio

import pytest

from adapters.tts.contracts import TtsAllFailed
from adapters.tts.fake_tts import FakeTts
from adapters.tts.fallback_chain import FallbackChainTts


def _run(coro):
    return asyncio.run(coro)


async def _text():
    yield "Hello there. This is a test."


def test_fallback_chain_uses_secondary_when_primary_first_byte_fails() -> None:
    primary = FakeTts(fail_mode="first_byte")
    fallback = FakeTts()
    chain = FallbackChainTts(primary, fallback)

    async def collect():
        return [c async for c in chain.synthesize(_text(), voice_id="v1")]

    chunks = _run(collect())
    assert len(chunks) == 2  # fallback successfully produced both sentence chunks


def test_fallback_chain_raises_when_both_providers_fail() -> None:
    primary = FakeTts(fail_mode="first_byte")
    fallback = FakeTts(fail_mode="all_fail")
    chain = FallbackChainTts(primary, fallback)

    async def collect():
        return [c async for c in chain.synthesize(_text(), voice_id="v1")]

    with pytest.raises(TtsAllFailed):
        _run(collect())


def test_fallback_chain_passes_through_when_primary_works() -> None:
    primary = FakeTts(bytes_per_char=4)
    fallback = FakeTts(fail_mode="all_fail")  # would fail if reached
    chain = FallbackChainTts(primary, fallback)

    async def collect():
        return [c async for c in chain.synthesize(_text(), voice_id="v1")]

    chunks = _run(collect())
    assert len(chunks) == 2
    # primary's bytes_per_char=4, so chunks larger than the default
    assert chunks[0] == b"\x00" * (len("Hello there.") * 4)
