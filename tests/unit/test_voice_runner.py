import asyncio
from collections.abc import AsyncIterator, Coroutine, Iterator
from pathlib import Path
from typing import Any

from adapters.http.voice_runner import VoiceRunner, VoiceTurnResult
from adapters.llm.router import ModelRouter
from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.tts.fake_tts import FakeTts
from core.eventlog import InMemoryEventLog
from core.events import AudioChunkAttached, LatencyObserved, SpeechFinalized

CASE_PATH = Path("templates/cases/multi_stage_case_v1.yaml")


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


async def _empty_audio() -> AsyncIterator[bytes]:
    for _ in range(2):
        yield b"\x00" * 320


class TextRouterProvider:
    """Minimal provider that returns plain text for streaming TTS."""

    def call(self, *, tier: str, prompt: str, stream: bool = False, timeout: float | None = None) -> str | Iterator[str]:
        del tier, prompt, timeout
        text = "Tell me more about your validation strategy."
        if stream:
            def gen() -> Iterator[str]:
                for i, w in enumerate(text.split(" ")):
                    yield (w if i == 0 else " " + w)
            return gen()
        return text


def _build_runner(transcript: str = "I would start with EDA", *, tts_mode: str = "off") -> VoiceRunner:
    log = InMemoryEventLog()
    router = ModelRouter(TextRouterProvider())
    stt = FakeStt(script=[SttPartial(transcript, is_final=True, elapsed_ms=600)])
    tts = FakeTts(bytes_per_char=2)
    return VoiceRunner(log=log, router=router, stt=stt, tts=tts, case_path=CASE_PATH, tts_mode=tts_mode)


def test_voice_runner_tts_on_full_turn_round_trip() -> None:
    runner = _build_runner(tts_mode="on")

    async def go() -> tuple[VoiceTurnResult, bytes, str]:
        sid = await runner.start_session(rubric_version="ds-ml-v1")
        result = await runner.next_turn(sid, candidate_audio=_empty_audio())
        # Must drain the audio_stream to fire the finally block (writes Latency event).
        audio_bytes = b"".join([c async for c in result.audio_stream])
        return result, audio_bytes, sid

    result, audio_bytes, sid = _run(go())
    assert isinstance(result, VoiceTurnResult)
    assert result.case_stage == "problem_framing"
    assert result.transcript == "I would start with EDA"
    assert len(audio_bytes) > 0
    assert result.ttft_ms >= 0
    # latency event recorded
    envelopes = runner.log.get_session(sid)
    assert any(isinstance(e.payload, LatencyObserved) for e in envelopes)


def test_voice_runner_records_speech_finalized_with_wpm_and_fillers() -> None:
    runner = _build_runner(transcript="um I think like the answer is yes", tts_mode="on")

    async def go() -> str:
        sid = await runner.start_session()
        result = await runner.next_turn(sid, candidate_audio=_empty_audio())
        async for _ in result.audio_stream:
            pass
        return sid

    sid = _run(go())
    envelopes = runner.log.get_session(sid)
    finals = [e.payload for e in envelopes if isinstance(e.payload, SpeechFinalized)]
    assert len(finals) == 1
    assert finals[0].filler_count >= 2  # "um", "like"
    assert finals[0].transcript == "um I think like the answer is yes"


def test_voice_runner_advances_case_stage_after_examiner_turn() -> None:
    runner = _build_runner(tts_mode="on")

    async def go() -> tuple[VoiceTurnResult, VoiceTurnResult, str]:
        sid = await runner.start_session()
        # Turn 1
        r1 = await runner.next_turn(sid, candidate_audio=_empty_audio())
        async for _ in r1.audio_stream:
            pass
        # Turn 2 — should now be in methodology stage
        r2 = await runner.next_turn(sid, candidate_audio=_empty_audio())
        async for _ in r2.audio_stream:
            pass
        return r1, r2, sid

    r1, r2, sid = _run(go())
    assert r1.case_stage == "problem_framing"
    assert r2.case_stage == "methodology"
    # Both LatencyObserved events present
    envelopes = runner.log.get_session(sid)
    assert sum(1 for e in envelopes if isinstance(e.payload, LatencyObserved)) == 2


def test_voice_runner_tts_off_by_default_records_text_but_no_audio() -> None:
    runner = _build_runner()

    async def go() -> tuple[bytes, str]:
        sid = await runner.start_session()
        result = await runner.next_turn(sid, candidate_audio=_empty_audio())
        audio_bytes = b"".join([c async for c in result.audio_stream])
        return audio_bytes, sid

    audio_bytes, sid = _run(go())
    envelopes = runner.log.get_session(sid)
    assert audio_bytes == b""
    assert any(isinstance(e.payload, LatencyObserved) for e in envelopes)
    assert not any(isinstance(e.payload, AudioChunkAttached) for e in envelopes)
    latency = next(e.payload for e in envelopes if isinstance(e.payload, LatencyObserved))
    assert latency.tts_first_byte_ms == 0
