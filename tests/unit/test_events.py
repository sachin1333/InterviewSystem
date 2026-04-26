from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.domain import Dimension
from core.events import (
    EVENT_TYPES,
    ArtifactAttached,
    AudioChunkAttached,
    BackchannelPosted,
    BreakDue,
    BudgetExceeded,
    CandidateIdle,
    CandidateJoined,
    ChallengerFailed,
    Envelope,
    ExaminerFailed,
    HumanOverride,
    IdleThresholdCrossed,
    LatencyObserved,
    ProfileIngested,
    RuntimeExecuted,
    RuntimeFailed,
    ScoreComputed,
    ScorerFailed,
    SessionEnded,
    SessionResumed,
    SessionStarted,
    SessionTimedOut,
    SignalEmitted,
    SpeechFinalized,
    SpeechStarted,
    TierFallback,
    TurnPosted,
    TurnRequested,
)

UTC = UTC


def test_envelope_is_monotonic_within_session():
    e1 = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC),
                  payload=SessionStarted(rubric_version="ds_mle@1"))
    e2 = Envelope(session_id="s1", seq=2, at=datetime(2026,4,22,10,0,1,tzinfo=UTC),
                  payload=SessionStarted(rubric_version="ds_mle@1"))
    assert e2.seq > e1.seq


def test_envelope_seq_must_be_positive():
    with pytest.raises(ValidationError):
        Envelope(session_id="s1", seq=0,
                 at=datetime(2026,4,22,tzinfo=UTC),
                 payload=SessionStarted(rubric_version="ds_mle@1"))


def test_session_started_accepts_policy_fields():
    e = SessionStarted(rubric_version="v1", target_answers=5, max_probes_per_prompt=2)
    assert e.target_answers == 5
    assert e.max_probes_per_prompt == 2


def test_session_started_defaults():
    e = SessionStarted(rubric_version="v1")
    assert e.target_answers == 3
    assert e.max_probes_per_prompt == 1


def test_runtime_executed_carries_timings():
    r = RuntimeExecuted(turn_id="t1", exit_code=0, wall_ms=120, artifact_id="a_out")
    assert r.wall_ms == 120


def test_runtime_failed_carries_reason():
    r = RuntimeFailed(turn_id="t1", reason="wall_time_exceeded", wall_ms=30000)
    assert r.reason == "wall_time_exceeded"


def test_scorer_failed_targets_dimension():
    f = ScorerFailed(dimension=Dimension.communication, reason="llm_timeout")
    assert f.dimension is Dimension.communication


def test_session_resumed_requires_from_seq():
    r = SessionResumed(from_seq=7)
    assert r.from_seq == 7


def test_human_override_carries_target_and_reason():
    h = HumanOverride(target_signal_dimension=Dimension.model_rationale,
                      original_value=0.4, override_value=0.7,
                      reviewer_id="rec-42", reason="model choice was justified")
    assert h.reviewer_id == "rec-42"


def test_session_timed_out_carries_timeout_kind():
    t = SessionTimedOut(reason="idle", elapsed_s=1800)
    assert t.reason == "idle"


def test_event_types_registry_covers_all_payloads():
    expected = {
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
    assert expected == EVENT_TYPES


def test_envelope_supports_idem_key():
    e = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC),
                 payload=SessionStarted(rubric_version="v1"), idem_key="nonce-abc")
    assert e.idem_key == "nonce-abc"
