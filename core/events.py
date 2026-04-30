from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.domain import (
    Actor,
    ArtifactKind,
    Dimension,
    ProblemId,
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


class PerProblemScoreComputed(_Evt):
    """Score breakdown for one problem in a multi-problem session."""

    problem_id: ProblemId
    ordinal: int = Field(ge=1)
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
    filler_count: int = Field(ge=0)      # "um", "uh" tally - feeds authenticity scorer
    degraded: bool = False


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


class TurnTimingObserved(_Evt):
    """Server-side timing checkpoints for one conversational turn (Phase 2.5).

    Values are milliseconds elapsed from the server receiving work for the
    turn. Cached paths use equal checkpoints; streaming paths can set
    ``first_token_ms`` independently from ``first_paint_ms``.
    """

    turn_id: str
    phase: Literal["candidate_submit", "challenger_opener", "examiner_probe"]
    submit_received_ms: int = Field(ge=0)
    context_assembled_ms: int = Field(ge=0)
    first_token_ms: int = Field(ge=0)
    first_paint_ms: int = Field(ge=0)


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
    declared_role: str = "DS / ML Engineer"
    years_experience: int | None = None
    declared_skills: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    user_md_path: str | None = None


class HumanOverride(_Evt):
    target_signal_dimension: Dimension
    original_value: float
    override_value: float
    reviewer_id: str
    reason: str
    source_refs: tuple[str, ...] = ()
    emitted_signal_id: str = ""

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("override reason is required")
        return value


class StageEntered(_Evt):
    stage_id: str
    primitive: str


class StageCompleted(_Evt):
    stage_id: str


# --- Phase 2.1: problem boundary events ----------------------------------

class ProblemIntroduced(_Evt):
    """Fired when the examiner presents a new problem to the candidate.

    ``problem_id``   - references a ``Problem.id`` in the session's problem list.
    ``opener_text``  - the narrow first question shown to the candidate.
    ``ordinal``      - 1-based position in the session's problem sequence
                       (Problem 1 of N, Problem 2 of N, ...).
    """

    problem_id: ProblemId
    opener_text: str
    ordinal: int = Field(ge=1)


class ProblemPlanSelected(_Evt):
    """Durable per-session problem sequence selected from an explicit source."""

    problem_ids: tuple[ProblemId, ...]
    bank_version: str | None = None
    source: Literal["explicit", "problem_bank"]


class ProblemClosed(_Evt):
    """Fired when the examiner decides the current problem is done.

    ``reason`` encodes *why* the problem was closed:
    - ``coverage_saturated`` - examiner judged rubric signal sufficient.
    - ``time_capped``        - wall-clock budget for the problem elapsed.
    - ``examiner_pivot``     - examiner chose to move on (editorial decision).
    - ``max_probes``         - safety cap on probes-per-problem fired.

    ``rationale`` is a short human-readable note produced by the examiner
    at close time (for recruiter dashboard and audit).
    """

    problem_id: ProblemId
    reason: Literal["coverage_saturated", "time_capped", "examiner_pivot", "max_probes"]
    rationale: str = ""


class CoverageSnapshot(_Evt):
    """Dim->signal snapshot at the moment a probe is generated (Phase 2.2).

    Persisted for replay and recruiter-dashboard audit.  ``signal_map`` is a
    plain dict so it survives JSON round-trips cleanly.
    """

    problem_id: ProblemId
    probe_count: int = Field(ge=0)
    signal_map: dict[str, float]   # Dimension.value -> accumulated signal
    under_served: list[str]        # Dimension.values that are below threshold


class ProblemCoverageObserved(_Evt):
    """Durable problem-scoped coverage signal used for examiner routing."""

    problem_id: ProblemId
    artifact_id: str
    dimension: Dimension
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["coverage_heuristic"] = "coverage_heuristic"


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
    SignalEmitted, ScoreComputed, PerProblemScoreComputed,
    # adapter failures
    ChallengerFailed, ExaminerFailed, ScorerFailed,
    # voice
    SpeechStarted, SpeechFinalized, AudioChunkAttached, LatencyObserved, TurnTimingObserved,
    # pacing
    CandidateIdle, IdleThresholdCrossed, BackchannelPosted, BreakDue,
    # intake / override
    ProfileIngested, HumanOverride,
    # case stages (legacy - kept for replay of Phase alpha-theta sessions)
    StageEntered, StageCompleted,
    # Phase 2.1: problem boundaries
    ProblemIntroduced, ProblemPlanSelected, ProblemClosed,
    # Phase 2.2: coverage tracker snapshot
    CoverageSnapshot, ProblemCoverageObserved,
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
