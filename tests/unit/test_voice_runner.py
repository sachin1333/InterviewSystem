import asyncio
from pathlib import Path

from adapters.http.voice_runner import VoiceRunner, VoiceTurnResult
from adapters.llm.router import ModelRouter
from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.tts.fake_tts import FakeTts
from core.eventlog import InMemoryEventLog
from core.events import LatencyObserved, SpeechFinalized

CASE_PATH = Path("templates/cases/multi_stage_case_v1.yaml")


def _run(coro):
    return asyncio.run(coro)


async def _empty_audio():
    for _ in range(2):
        yield b"\x00" * 320


class TextRouterProvider:
    """Minimal provider that returns plain text for streaming TTS."""

    def call(self, *, tier, prompt, stream=False, timeout=None):
        text = "Tell me more about your validation strategy."
        if stream:
            def gen():
                for i, w in enumerate(text.split(" ")):
                    yield (w if i == 0 else " " + w)
            return gen()
        return text


def _build_runner(transcript: str = "I would start with EDA") -> VoiceRunner:
    log = InMemoryEventLog()
    router = ModelRouter(TextRouterProvider())
    stt = FakeStt(script=[SttPartial(transcript, is_final=True, elapsed_ms=600)])
    tts = FakeTts(bytes_per_char=2)
    return VoiceRunner(log=log, router=router, stt=stt, tts=tts, case_path=CASE_PATH)


def test_voice_runner_full_turn_round_trip() -> None:
    runner = _build_runner()

    async def go():
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
    runner = _build_runner(transcript="um I think like the answer is yes")

    async def go():
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
    runner = _build_runner()

    async def go():
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
