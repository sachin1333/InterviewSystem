"""VoiceRunner — streaming STT → LLM → TTS pipeline for voice interview turns.

Sibling to SessionRunner. Async-native. Owns event-log writes for all voice turns.
Stateless across calls: each next_turn replays the log to recover the current case stage.
"""
from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from adapters.llm.router import ModelRouter
from adapters.stt.contracts import Stt
from adapters.tts.contracts import Tts
from core.case_loader import CaseDefinition, CaseStage, load_case
from core.contracts import EventLog
from core.domain import Actor, ArtifactKind, TurnKind
from core.events import (
    ArtifactAttached,
    AudioChunkAttached,
    Envelope,
    LatencyObserved,
    SessionStarted,
    SpeechFinalized,
    SpeechStarted,
    TurnPosted,
)

DEFAULT_CASE_PATH = Path("templates/cases/multi_stage_case_v1.yaml")


@dataclass(frozen=True)
class VoiceTurnResult:
    session_id: str
    turn_id: str
    case_stage: str
    transcript: str
    audio_stream: AsyncIterator[bytes]
    ttft_ms: int  # LLM time-to-first-token
    end_to_end_ms: int


class VoiceRunner:
    def __init__(
        self,
        *,
        log: EventLog,
        router: ModelRouter,
        stt: Stt,
        tts: Tts,
        case_path: str | Path = DEFAULT_CASE_PATH,
        voice_id: str = "default",
        rubric_version: str = "ds-ml-v1",
    ) -> None:
        self.log = log
        self.router = router
        self.stt = stt
        self.tts = tts
        self.case: CaseDefinition = load_case(case_path)
        self.voice_id = voice_id
        self.rubric_version = rubric_version

    # --- public API --------------------------------------------------------

    async def start_session(self, *, rubric_version: str | None = None) -> str:
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        rv = rubric_version or self.rubric_version
        self._append(session_id, SessionStarted(rubric_version=rv))
        return session_id

    async def next_turn(
        self,
        session_id: str,
        *,
        candidate_audio: AsyncIterator[bytes],
        sample_rate_hz: int = 16000,
    ) -> VoiceTurnResult:
        """One end-to-end voice turn. Returns when the LLM response is ready to stream."""
        run_started = time.monotonic()

        # Determine the current case stage by counting prior examiner turns.
        stage = self._current_stage(session_id)

        # 1. STT: consume candidate audio, capture transcript + timing.
        candidate_turn_id = f"turn-{uuid.uuid4().hex[:10]}"
        self._append(session_id, SpeechStarted(
            turn_id=candidate_turn_id,
            started_at_ms=int((time.monotonic() - run_started) * 1000),
        ))

        transcript = ""
        first_partial_ms = 0
        final_ms = 0
        seen_partial = False
        stt_started = time.monotonic()
        async for partial in self.stt.stream(candidate_audio, sample_rate_hz=sample_rate_hz):
            if not seen_partial:
                first_partial_ms = int((time.monotonic() - stt_started) * 1000)
                seen_partial = True
            if partial.is_final:
                transcript = partial.text
                final_ms = int((time.monotonic() - stt_started) * 1000)
                break
            transcript = partial.text  # carry latest partial in case of mid-stream end

        wpm = _estimate_wpm(transcript, final_ms)
        filler_count = _count_fillers(transcript)
        self._append(session_id, SpeechFinalized(
            turn_id=candidate_turn_id,
            transcript=transcript,
            wpm=wpm,
            first_partial_ms=first_partial_ms,
            final_ms=final_ms,
            filler_count=filler_count,
        ))
        # Record candidate turn + transcript artifact.
        candidate_artifact_id = f"art-{uuid.uuid4().hex[:10]}"
        self._append(session_id, TurnPosted(
            id=candidate_turn_id,
            actor=Actor.candidate,
            kind=TurnKind.spoken_answer,
        ))
        self._append(session_id, ArtifactAttached(
            id=candidate_artifact_id,
            kind=ArtifactKind.transcript,
            version=1,
            content=transcript,
            produced_by_turn_id=candidate_turn_id,
        ))

        # 2. LLM streaming with TTFT metrics.
        prompt = self._build_prompt(stage, transcript)
        metrics = self.router.call_streaming_with_metrics(tier="mid", prompt=prompt)
        llm_ttft_ms = metrics.ttft_ms

        # 3. TTS: stream synthesizer over the LLM text.
        text_for_tts = metrics.text
        tts_first_byte_holder = {"ms": 0}

        async def _text_chunks() -> AsyncIterator[str]:
            yield text_for_tts

        synth_started = time.monotonic()
        synth_iter = self.tts.synthesize(_text_chunks(), voice_id=self.voice_id)

        async def _audio_with_timing() -> AsyncIterator[bytes]:
            first = True
            total_bytes = 0
            try:
                async for chunk in synth_iter:
                    if first and chunk:
                        tts_first_byte_holder["ms"] = int((time.monotonic() - synth_started) * 1000)
                        first = False
                    total_bytes += len(chunk)
                    yield chunk
            finally:
                examiner_turn_id = f"turn-{uuid.uuid4().hex[:10]}"
                examiner_artifact_id = f"art-{uuid.uuid4().hex[:10]}"
                self._append(session_id, TurnPosted(
                    id=examiner_turn_id,
                    actor=Actor.examiner,
                    kind=TurnKind.spoken_question,
                ))
                self._append(session_id, ArtifactAttached(
                    id=examiner_artifact_id,
                    kind=ArtifactKind.transcript,
                    version=1,
                    content=text_for_tts,
                    produced_by_turn_id=examiner_turn_id,
                ))
                if total_bytes:
                    audio_artifact_id = f"art-{uuid.uuid4().hex[:10]}"
                    self._append(session_id, AudioChunkAttached(
                        turn_id=examiner_turn_id,
                        artifact_id=audio_artifact_id,
                        duration_ms=int((time.monotonic() - synth_started) * 1000),
                        bytes=total_bytes,
                    ))
                end_to_end_ms = int((time.monotonic() - run_started) * 1000)
                self._append(session_id, LatencyObserved(
                    turn_id=examiner_turn_id,
                    stt_first_partial_ms=first_partial_ms,
                    stt_final_ms=final_ms,
                    llm_ttft_ms=llm_ttft_ms,
                    tts_first_byte_ms=tts_first_byte_holder["ms"],
                    end_to_end_ms=end_to_end_ms,
                ))

        return VoiceTurnResult(
            session_id=session_id,
            turn_id=candidate_turn_id,
            case_stage=stage.id,
            transcript=transcript,
            audio_stream=_audio_with_timing(),
            ttft_ms=llm_ttft_ms,
            end_to_end_ms=int((time.monotonic() - run_started) * 1000),
        )

    # --- internals ---------------------------------------------------------

    def _current_stage(self, session_id: str) -> CaseStage:
        """Pick the next case stage based on how many spoken_question turns the examiner has posted."""
        sequence = self.case.stage_sequence()
        envelopes = self.log.get_session(session_id)
        examiner_turns = sum(
            1 for e in envelopes
            if isinstance(e.payload, TurnPosted)
            and e.payload.actor == Actor.examiner
            and e.payload.kind in (TurnKind.spoken_question, TurnKind.spoken_probe)
        )
        idx = min(examiner_turns, len(sequence) - 1)
        return sequence[idx]

    def _build_prompt(self, stage: CaseStage, transcript: str) -> str:
        return (
            f"You are interviewing a data scientist. Stage: {stage.id} (primitive: {stage.primitive}).\n"
            f"Seed: {stage.prompt_seed}\n"
            f"Candidate just said: {transcript!r}\n"
            f"Reply in 1-2 sentences."
        )

    def _append(self, session_id: str, payload: object) -> None:
        seq = (self.log.last_seq(session_id) or 0) + 1
        self.log.append(Envelope(
            session_id=session_id,
            seq=seq,
            at=datetime.now(UTC),
            payload=payload,
        ))


_SINGLE_FILLERS = {"um", "uh", "like"}
_MULTI_FILLERS = ["you know"]
_WORD_RE = re.compile(r"[a-z']+")


def _count_fillers(text: str) -> int:
    if not text:
        return 0
    lc = text.lower()
    tokens = _WORD_RE.findall(lc)
    count = sum(1 for t in tokens if t in _SINGLE_FILLERS)
    for phrase in _MULTI_FILLERS:
        count += lc.count(phrase)
    return count


def _estimate_wpm(text: str, duration_ms: int) -> int:
    if duration_ms <= 0 or not text:
        return 0
    words = len(text.split())
    seconds = duration_ms / 1000.0
    if seconds <= 0:
        return 0
    return int(words / seconds * 60)
