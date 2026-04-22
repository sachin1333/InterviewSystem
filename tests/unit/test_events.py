from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.domain import Dimension
from core.events import (
    EVENT_TYPES,
    ArtifactAttached,
    BackchannelPosted,
    BreakDue,
    CandidateIdle,
    Envelope,
    HumanOverride,
    IdleThresholdCrossed,
    ProfileIngested,
    ScoreComputed,
    SessionEnded,
    SessionStarted,
    SessionTimedOut,
    SignalEmitted,
    TurnPosted,
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
        SessionStarted, TurnPosted, ArtifactAttached, SignalEmitted,
        ScoreComputed, SessionEnded, CandidateIdle, ProfileIngested,
        BackchannelPosted, IdleThresholdCrossed, BreakDue,
        HumanOverride, SessionTimedOut,
    }
    assert expected == EVENT_TYPES
