from __future__ import annotations

import asyncio
from pathlib import Path

from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.voice_runner import VoiceRunner
from adapters.llm.router import ModelRouter
from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.tts.fake_tts import FakeTts
from adapters.tts.fallback_chain import FallbackChainTts
from core.events import SpeechFinalized, TierFallback


async def _audio() -> asyncio.AsyncIterator[bytes]:
    for _ in range(3):
        yield b"\x00" * 320


class _VoiceProvider:
    def call(self, *, tier: str, prompt: str, stream: bool = False, timeout: float | None = None):
        del tier, prompt, timeout
        text = "Thanks — keep going."
        if stream:
            return iter(["Thanks", " — ", "keep going."])
        return text


def test_voice_midstream_stt_disconnect_preserves_partial_transcript(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "stt-chaos.db")
    runner = VoiceRunner(
        log=log,
        router=ModelRouter(_VoiceProvider()),
        stt=FakeStt(
            script=[
                SttPartial("I'd start", is_final=False, elapsed_ms=150),
                SttPartial("I'd start with the target metric", is_final=True, elapsed_ms=600),
            ],
            fail_mode="midstream",
        ),
        tts=FakeTts(bytes_per_char=2),
    )

    async def go() -> str:
        session_id = await runner.start_session()
        result = await runner.next_turn(session_id, candidate_audio=_audio())
        async for _ in result.audio_stream:
            pass
        return session_id

    session_id = asyncio.run(go())
    finals = [
        env.payload for env in log.get_session(session_id) if isinstance(env.payload, SpeechFinalized)
    ]
    assert len(finals) == 1
    assert finals[0].degraded is True
    assert finals[0].transcript == "I'd start"


def test_voice_tts_fallback_records_tier_fallback_event(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "tts-chaos.db")
    tts = FallbackChainTts(
        FakeTts(bytes_per_char=2, fail_mode="first_byte"),
        FakeTts(bytes_per_char=2),
    )
    runner = VoiceRunner(
        log=log,
        router=ModelRouter(_VoiceProvider()),
        stt=FakeStt(script=[SttPartial("I would compare against the baseline.", True, 400)]),
        tts=tts,
    )

    async def go() -> tuple[str, int]:
        session_id = await runner.start_session()
        result = await runner.next_turn(session_id, candidate_audio=_audio())
        total = 0
        async for chunk in result.audio_stream:
            total += len(chunk)
        return session_id, total

    session_id, total = asyncio.run(go())
    assert total > 0
    assert any(isinstance(env.payload, TierFallback) for env in log.get_session(session_id))
