from __future__ import annotations

import asyncio
from pathlib import Path

from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.voice_runner import VoiceRunner
from adapters.llm.router import ModelRouter
from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.tts.fake_tts import FakeTts
from core.events import LatencyObserved


async def _audio() -> asyncio.AsyncIterator[bytes]:
    yield b"\x00" * 320


class _VoiceProvider:
    def call(self, *, tier: str, prompt: str, stream: bool = False, timeout: float | None = None):
        del tier, prompt, timeout
        if stream:
            return iter(["Short", " answer."])
        return "Short answer."


class _StepClock:
    def __init__(self, step: float = 0.08) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_voice_latency_budget_p50_stays_under_800ms(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "latency.db")
    clock = _StepClock(step=0.08)
    runner = VoiceRunner(
        log=log,
        router=ModelRouter(_VoiceProvider()),
        stt=FakeStt(script=[SttPartial("Concise answer.", True, 300)]),
        tts=FakeTts(bytes_per_char=2),
        clock=clock,
    )

    async def go() -> str:
        session_id = await runner.start_session()
        for _ in range(10):
            result = await runner.next_turn(session_id, candidate_audio=_audio())
            async for _ in result.audio_stream:
                pass
        return session_id

    session_id = asyncio.run(go())
    samples = sorted(
        env.payload.end_to_end_ms
        for env in log.get_session(session_id)
        if isinstance(env.payload, LatencyObserved)
    )
    assert len(samples) == 10
    p50 = samples[len(samples) // 2]
    assert p50 <= 800
