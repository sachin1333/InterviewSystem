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


class SessionStarted(_Evt):
    rubric_version: str


class TurnPosted(_Evt):
    id: str
    actor: Actor
    kind: TurnKind


class ArtifactAttached(_Evt):
    id: str
    kind: ArtifactKind
    produced_by_turn_id: str
    version: int = Field(ge=1)


class SignalEmitted(_Evt):
    signal: Signal


class ScoreComputed(_Evt):
    score: Score


class SessionEnded(_Evt):
    reason: str | None = None


class CandidateIdle(_Evt):
    idle_seconds: int


class ProfileIngested(_Evt):
    profile_id: str


class BackchannelPosted(_Evt):
    message: str


class IdleThresholdCrossed(_Evt):
    threshold_s: int


class BreakDue(_Evt):
    reason: str


class HumanOverride(_Evt):
    target_signal_dimension: Dimension
    original_value: float
    override_value: float
    reviewer_id: str
    reason: str


class SessionTimedOut(_Evt):
    reason: str
    elapsed_s: int


EVENT_TYPES: set[type] = {
    SessionStarted, TurnPosted, ArtifactAttached, SignalEmitted,
    ScoreComputed, SessionEnded, CandidateIdle, ProfileIngested,
    BackchannelPosted, IdleThresholdCrossed, BreakDue,
    HumanOverride, SessionTimedOut,
}


class Envelope(_Evt):
    session_id: str
    seq: int = Field(gt=0)
    at: datetime
    payload: Any

    @field_validator("payload")
    @classmethod
    def _check_payload(cls, v: Any) -> Any:
        if not isinstance(v, BaseModel):
            raise TypeError("payload must be a pydantic model instance")
        if type(v) not in EVENT_TYPES:
            raise TypeError(f"unknown payload type: {type(v)}")
        return v
