"""VoiceRunner — streaming STT → LLM → TTS pipeline for voice interview turns.

Sibling to SessionRunner. Async-native. Owns event-log writes for all voice turns.
Stateless across calls: each next_turn replays the log to recover the current case stage.
"""
from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from adapters.llm.router import ModelRouter
from adapters.stt.contracts import Stt, SttConnectionError, SttTimeout
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
    SignalEmitted,
    SpeechFinalized,
    SpeechStarted,
    TierFallback,
    TurnPosted,
)

if TYPE_CHECKING:
    from adapters.scorer.authenticity_scorer import AuthenticityScorer
    from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
    from core.primitives import Primitive

DEFAULT_CASE_PATH = Path("templates/cases/multi_stage_case_v1.yaml")


@dataclass(frozen=True)
class VoiceTurnResult:
    session_id: str
    turn_id: str
    case_stage: str
    transcript: str
    examiner_text: str
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
        tts_mode: str = "off",
        communication_scorer: LlmCommunicationScorer | None = None,
        authenticity_scorer: AuthenticityScorer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.log = log
        self.router = router
        self.stt = stt
        self.tts = tts
        self.case: CaseDefinition = load_case(case_path)
        self.voice_id = voice_id
        self.rubric_version = rubric_version
        normalized_tts_mode = tts_mode.strip().lower() or "off"
        self.tts_mode = normalized_tts_mode if normalized_tts_mode in {"off", "on"} else "off"
        self.communication_scorer = communication_scorer
        self.authenticity_scorer = authenticity_scorer
        self.clock = clock

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
        run_started = self.clock()

        # Determine the current case stage by counting prior examiner turns.
        stage = self._current_stage(session_id)

        # 1. STT: consume candidate audio, capture transcript + timing.
        candidate_turn_id = f"turn-{uuid.uuid4().hex[:10]}"
        self._append(session_id, SpeechStarted(
            turn_id=candidate_turn_id,
            started_at_ms=int((self.clock() - run_started) * 1000),
        ))

        transcript = ""
        first_partial_ms = 0
        final_ms = 0
        seen_partial = False
        stt_started = self.clock()
        degraded = False
        try:
            async for partial in self.stt.stream(candidate_audio, sample_rate_hz=sample_rate_hz):
                if not seen_partial:
                    first_partial_ms = int((self.clock() - stt_started) * 1000)
                    seen_partial = True
                if partial.is_final:
                    transcript = partial.text
                    final_ms = int((self.clock() - stt_started) * 1000)
                    break
                transcript = partial.text  # carry latest partial in case of mid-stream end
        except (SttConnectionError, SttTimeout):
            degraded = True
            final_ms = int((self.clock() - stt_started) * 1000)

        wpm = _estimate_wpm(transcript, final_ms)
        filler_count = _count_fillers(transcript)
        self._append(session_id, SpeechFinalized(
            turn_id=candidate_turn_id,
            transcript=transcript,
            wpm=wpm,
            first_partial_ms=first_partial_ms,
            final_ms=final_ms,
            filler_count=filler_count,
            degraded=degraded,
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
        if self.communication_scorer is not None and transcript:
            comm_signal = self.communication_scorer.score_voice(
                transcript,
                filler_count=filler_count,
                wpm=wpm,
                artifact_id=candidate_artifact_id,
                session_id=session_id,
            )
            self._append(session_id, SignalEmitted(signal=comm_signal))

        # 2. LLM streaming with TTFT metrics.
        prompt = self._build_prompt(stage, transcript)
        metrics = self.router.call_streaming_with_metrics(tier="mid", prompt=prompt)
        llm_ttft_ms = metrics.ttft_ms

        # 3. TTS: stream synthesizer over the LLM text only when explicitly enabled.
        text_for_tts = metrics.text
        tts_first_byte_holder = {"ms": 0}

        async def _text_chunks() -> AsyncIterator[str]:
            yield text_for_tts

        synth_started = self.clock()

        async def _audio_with_timing() -> AsyncIterator[bytes]:
            first = True
            total_bytes = 0
            try:
                if self.tts_mode == "on":
                    synth_iter = self.tts.synthesize(_text_chunks(), voice_id=self.voice_id)
                    async for chunk in synth_iter:
                        if first and chunk:
                            tts_first_byte_holder["ms"] = int((self.clock() - synth_started) * 1000)
                            first = False
                        total_bytes += len(chunk)
                        yield chunk
            finally:
                self._emit_tts_fallback_events(session_id)
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
                        duration_ms=int((self.clock() - synth_started) * 1000),
                        bytes=total_bytes,
                    ))
                end_to_end_ms = int((self.clock() - run_started) * 1000)
                latency = LatencyObserved(
                    turn_id=examiner_turn_id,
                    stt_first_partial_ms=first_partial_ms,
                    stt_final_ms=final_ms,
                    llm_ttft_ms=llm_ttft_ms,
                    tts_first_byte_ms=tts_first_byte_holder["ms"],
                    end_to_end_ms=end_to_end_ms,
                )
                self._append(session_id, latency)
                if self.authenticity_scorer is not None:
                    authenticity_signal = self.authenticity_scorer.score(
                        self._session_latencies(session_id),
                        self._session_speeches(session_id),
                        counterfactual_handled=_counterfactual_handled(stage.primitive, transcript),
                        generic_counterfactual_response=_generic_counterfactual_response(
                            stage.primitive, transcript,
                        ),
                        resume_specificity_score=_resume_specificity_score(stage.primitive, transcript),
                        source_refs=(candidate_artifact_id,),
                    )
                    self._append(session_id, SignalEmitted(signal=authenticity_signal))

        return VoiceTurnResult(
            session_id=session_id,
            turn_id=candidate_turn_id,
            case_stage=stage.id,
            transcript=transcript,
            examiner_text=text_for_tts,
            audio_stream=_audio_with_timing(),
            ttft_ms=llm_ttft_ms,
            end_to_end_ms=int((self.clock() - run_started) * 1000),
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

    def _emit_tts_fallback_events(self, session_id: str) -> None:
        consume_events = getattr(self.tts, "consume_events", None)
        if not callable(consume_events):
            return
        for event in consume_events():
            if event == "tier_fallback":
                self._append(session_id, TierFallback(
                    from_tier="primary",
                    to_tier="fallback",
                    reason="tts provider fallback",
                ))

    def _session_latencies(self, session_id: str) -> list[LatencyObserved]:
        return [
            env.payload
            for env in self.log.get_session(session_id)
            if isinstance(env.payload, LatencyObserved)
        ]

    def _session_speeches(self, session_id: str) -> list[SpeechFinalized]:
        return [
            env.payload
            for env in self.log.get_session(session_id)
            if isinstance(env.payload, SpeechFinalized)
        ]

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


def _counterfactual_handled(primitive: Primitive, transcript: str) -> bool:
    if primitive.value != "counterfactual":
        return True
    text = transcript.strip().lower()
    if len(text.split()) < 6:
        return False
    return not _generic_counterfactual_response(primitive, transcript)


def _generic_counterfactual_response(primitive: Primitive, transcript: str) -> bool:
    if primitive.value != "counterfactual":
        return False
    text = transcript.strip().lower()
    generic_phrases = (
        "it depends",
        "align to the business goal",
        "choose the right model",
        "look at the data",
    )
    return any(phrase in text for phrase in generic_phrases) or len(text.split()) < 10


def _resume_specificity_score(primitive: Primitive, transcript: str) -> float:
    if primitive.value != "resume_deep_dive":
        return 0.8
    has_digits = any(ch.isdigit() for ch in transcript)
    text = transcript.lower()
    detail_hits = sum(token in text for token in ("ratio", "%", "rows", "million", "weekly"))
    if has_digits and detail_hits >= 1:
        return 1.0
    if has_digits or detail_hits >= 1:
        return 0.6
    return 0.25
