import asyncio

import pytest

from adapters.stt.contracts import SttConnectionError, SttPartial
from adapters.stt.fake_stt import FakeStt


async def _empty_audio():
    for _ in range(2):
        yield b""


def _run(coro):
    return asyncio.run(coro)


def test_fake_stt_emits_scripted_partials_then_final() -> None:
    fake = FakeStt(script=[
        SttPartial("hel", is_final=False, elapsed_ms=100),
        SttPartial("hello", is_final=False, elapsed_ms=200),
        SttPartial("hello world", is_final=True, elapsed_ms=550),
    ])

    async def collect():
        out: list[SttPartial] = []
        async for p in fake.stream(_empty_audio()):
            out.append(p)
        return out

    result = _run(collect())
    assert [p.text for p in result] == ["hel", "hello", "hello world"]
    assert result[-1].is_final


def test_fake_stt_empty_script_yields_nothing() -> None:
    fake = FakeStt(script=[])

    async def collect():
        out = []
        async for p in fake.stream(_empty_audio()):
            out.append(p)
        return out

    assert _run(collect()) == []


def test_fake_stt_connect_failure_raises() -> None:
    fake = FakeStt(script=[], fail_mode="connect")

    async def collect():
        async for _ in fake.stream(_empty_audio()):
            pass

    with pytest.raises(SttConnectionError):
        _run(collect())
