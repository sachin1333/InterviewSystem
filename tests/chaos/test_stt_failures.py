import asyncio

import pytest

from adapters.stt.contracts import SttConnectionError, SttPartial
from adapters.stt.fake_stt import FakeStt


async def _audio():
    for _ in range(3):
        yield b"\x00" * 320


def _run(coro):
    return asyncio.run(coro)


def test_midstream_disconnect_preserves_partial_transcript() -> None:
    """Caller sees partials emitted before the disconnect, then SttConnectionError."""
    fake = FakeStt(
        script=[
            SttPartial("hel", is_final=False, elapsed_ms=100),
            SttPartial("hello", is_final=False, elapsed_ms=250),
            SttPartial("hello world", is_final=True, elapsed_ms=600),
            SttPartial("hello world final", is_final=True, elapsed_ms=900),
        ],
        fail_mode="midstream",
    )
    collected: list[SttPartial] = []

    async def collect():
        async for p in fake.stream(_audio()):
            collected.append(p)

    with pytest.raises(SttConnectionError):
        _run(collect())
    assert len(collected) >= 1, "at least one partial should arrive before disconnect"
    assert all(not p.is_final for p in collected), "no final emitted in midstream failure"


def test_no_partials_path_yields_nothing() -> None:
    fake = FakeStt(
        script=[SttPartial("anything", is_final=True, elapsed_ms=300)],
        fail_mode="no_partials",
    )

    async def collect():
        out = []
        async for p in fake.stream(_audio()):
            out.append(p)
        return out

    assert _run(collect()) == []


def test_connection_failure_propagates_before_any_partial() -> None:
    fake = FakeStt(script=[], fail_mode="connect")

    async def collect():
        async for _ in fake.stream(_audio()):
            pass

    with pytest.raises(SttConnectionError):
        _run(collect())
