from __future__ import annotations

import asyncio
from pathlib import Path

from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.voice_runner import VoiceRunner
from adapters.llm.router import ModelRouter
from adapters.scorer.authenticity_scorer import AuthenticityScorer
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.tts.fake_tts import FakeTts
from core.events import LatencyObserved, SignalEmitted


async def _audio() -> asyncio.AsyncIterator[bytes]:
    for _ in range(2):
        yield b"\x00" * 320


class _VoiceProvider:
    def call(self, *, tier: str, prompt: str, stream: bool = False, timeout: float | None = None):
        del tier, prompt, timeout
        text = "Let's go one level deeper on your approach."
        if stream:
            return iter(["Let's ", "go ", "one ", "level ", "deeper."])
        return text


def test_e2e_voice_happy_path_walks_all_five_stages(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "voice.db")
    router = ModelRouter(_VoiceProvider())
    runner = VoiceRunner(
        log=log,
        router=router,
        stt=FakeStt(script=[SttPartial("I would start with the metric and baseline.", True, 500)]),
        tts=FakeTts(bytes_per_char=2),
        communication_scorer=LlmCommunicationScorer(router),
        authenticity_scorer=AuthenticityScorer(),
    )

    async def go() -> tuple[str, list[str]]:
        session_id = await runner.start_session()
        stages: list[str] = []
        for _ in range(5):
            result = await runner.next_turn(session_id, candidate_audio=_audio())
            stages.append(result.case_stage)
            async for _ in result.audio_stream:
                pass
        return session_id, stages

    session_id, stages = asyncio.run(go())
    assert stages == [
        "problem_framing",
        "methodology",
        "execution",
        "interpretation",
        "synthesis",
    ]

    events = log.get_session(session_id)
    assert sum(isinstance(env.payload, LatencyObserved) for env in events) == 5
    authenticity_signals = [
        env.payload.signal
        for env in events
        if isinstance(env.payload, SignalEmitted)
        and env.payload.signal.dimension.value == "response_authenticity"
    ]
    assert len(authenticity_signals) == 5
