import asyncio

import pytest

from adapters.tts.contracts import TtsTimeout
from adapters.tts.fake_tts import FakeTts


def _run(coro):
    return asyncio.run(coro)


async def _text(*parts: str):
    for p in parts:
        yield p


def test_fake_tts_emits_one_audio_chunk_per_sentence() -> None:
    fake = FakeTts(bytes_per_char=2)

    async def collect():
        return [c async for c in fake.synthesize(_text("Hi there. How are you?"), voice_id="v1")]

    chunks = _run(collect())
    assert len(chunks) == 2
    assert all(isinstance(c, bytes) for c in chunks)
    assert all(len(c) > 0 for c in chunks)


def test_fake_tts_first_byte_failure_raises() -> None:
    fake = FakeTts(fail_mode="first_byte")

    async def collect():
        return [c async for c in fake.synthesize(_text("hello"), voice_id="v1")]

    with pytest.raises(TtsTimeout):
        _run(collect())


def test_fake_tts_empty_input_yields_nothing() -> None:
    fake = FakeTts()

    async def collect():
        return [c async for c in fake.synthesize(_text("", "  "), voice_id="v1")]

    assert _run(collect()) == []
