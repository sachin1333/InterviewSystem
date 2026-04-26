from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.domain import (
    Actor,
    ArtifactKind,
    Dimension,
    Score,
    Signal,
    TurnKind,
)


class _Evt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- session lifecycle ----------------------------------------------------

class SessionStarted(_Evt):
    rubric_version: str
    target_answers: int = Field(default=3, ge=1)
    max_probes_per_prompt: int = Field(default=1, ge=0)
    pack_id: str = "ds-ml-v1"  # default for back-compat


class CandidateJoined(_Evt):
    candidate_handle: str


class SessionResumed(_Evt):
    from_seq: int = Field(ge=1)


class SessionEnded(_Evt):
    reason: str | None = None


class SessionTimedOut(_Evt):
    reason: str
    elapsed_s: int


# --- cost tracking --------------------------------------------------------

class BudgetExceeded(_Evt):
    reason: str
    est_cost_usd: float


class TierFallback(_Evt):
    from_tier: str
    to_tier: str
    reason: str


# --- conversational flow --------------------------------------------------

class TurnRequested(_Evt):
    """Orchestrator dispatched a request to an adapter. Audit trail."""
    of: Actor
    kind: TurnKind


class TurnPosted(_Evt):
    id: str
    actor: Actor
    kind: TurnKind


class ArtifactAttached(_Evt):
    id: str
    kind: ArtifactKind
    produced_by_turn_id: str
    version: int = Field(ge=1)
    content: str | None = None


# --- runtime execution ----------------------------------------------------

class RuntimeExecuted(_Evt):
    turn_id: str
    exit_code: int
    wall_ms: int = Field(ge=0)
    artifact_id: str  # cell_output artifact


class RuntimeFailed(_Evt):
    turn_id: str
    reason: str
    wall_ms: int = Field(ge=0)


# --- scoring --------------------------------------------------------------

class SignalEmitted(_Evt):
    signal: Signal


class ScoreComputed(_Evt):
    score: Score


# --- adapter failure audit -----------------------------------------------

class ChallengerFailed(_Evt):
    reason: str


class ExaminerFailed(_Evt):
    reason: str


class ScorerFailed(_Evt):
    dimension: Dimension
    reason: str


# --- voice / speech lifecycle --------------------------------------------

class SpeechStarted(_Evt):
    """Candidate began speaking; VAD onset."""
    turn_id: str
    started_at_ms: int = Field(ge=0)


class SpeechFinalized(_Evt):
    """Candidate finished speaking; STT final result available."""
    turn_id: str
    transcript: str
    wpm: int = Field(ge=0)
    first_partial_ms: int = Field(ge=0)  # time-to-first-partial-transcript
    final_ms: int = Field(ge=0)          # total speech duration
    filler_count: int = Field(ge=0)      # "um", "uh" tally — feeds authenticity scorer


class AudioChunkAttached(_Evt):
    """Pointer to the persisted audio blob for a turn (artifact_ref pattern)."""
    turn_id: str
    artifact_id: str
    duration_ms: int = Field(ge=0)
    bytes: int = Field(ge=0)


class LatencyObserved(_Evt):
    """Turn-level latency record for cheating-defense + ops dashboard."""
    turn_id: str
    stt_first_partial_ms: int = Field(ge=0)
    stt_final_ms: int = Field(ge=0)
    llm_ttft_ms: int = Field(ge=0)
    tts_first_byte_ms: int = Field(ge=0)
    end_to_end_ms: int = Field(ge=0)


# --- candidate behavior / pacing -----------------------------------------

class CandidateIdle(_Evt):
    idle_seconds: int


class IdleThresholdCrossed(_Evt):
    threshold_s: int


class BackchannelPosted(_Evt):
    message: str


class BreakDue(_Evt):
    reason: str


# --- intake / human override ---------------------------------------------

class ProfileIngested(_Evt):
    profile_id: str


class HumanOverride(_Evt):
    target_signal_dimension: Dimension
    original_value: float
    override_value: float
    reviewer_id: str
    reason: str


EVENT_TYPES: set[type] = {
    # lifecycle
    SessionStarted, CandidateJoined, SessionResumed, SessionEnded, SessionTimedOut,
    # cost tracking
    BudgetExceeded, TierFallback,
    # flow
    TurnRequested, TurnPosted, ArtifactAttached,
    # runtime
    RuntimeExecuted, RuntimeFailed,
    # scoring
    SignalEmitted, ScoreComputed,
    # adapter failures
    ChallengerFailed, ExaminerFailed, ScorerFailed,
    # voice
    SpeechStarted, SpeechFinalized, AudioChunkAttached, LatencyObserved,
    # pacing
    CandidateIdle, IdleThresholdCrossed, BackchannelPosted, BreakDue,
    # intake / override
    ProfileIngested, HumanOverride,
}


class Envelope(_Evt):
    session_id: str
    seq: int = Field(gt=0)
    at: datetime
    payload: Any
    idem_key: str | None = None

    @field_validator("payload")
    @classmethod
    def _check_payload(cls, v: Any) -> Any:
        if not isinstance(v, BaseModel):
            raise TypeError("payload must be a pydantic model instance")
        if type(v) not in EVENT_TYPES:
            raise TypeError(f"unknown payload type: {type(v)}")
        return v
