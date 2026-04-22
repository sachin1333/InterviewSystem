from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.domain import (
    Actor,
    Artifact,
    ArtifactKind,
    Dimension,
    Rubric,
    Score,
    Session,
    Signal,
    Turn,
    TurnKind,
)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_session_minimal_construction():
    s = Session(id="s1", rubric_version="ds_mle@1", started_at=_utc("2026-04-22T10:00:00"))
    assert s.id == "s1"
    assert s.turns == ()
    assert s.artifacts == ()


def test_turn_accepts_known_actor_and_kind():
    t = Turn(id="t1", actor=Actor.candidate, kind=TurnKind.answer,
             prompt_ref=None, produced_artifact_refs=(), at=_utc("2026-04-22T10:00:05"))
    assert t.actor is Actor.candidate
    assert t.kind is TurnKind.answer


def test_turn_rejects_unknown_actor():
    with pytest.raises(ValidationError):
        Turn(id="t1", actor="martian", kind=TurnKind.answer,
             prompt_ref=None, produced_artifact_refs=(),
             at=_utc("2026-04-22T10:00:05"))


def test_artifact_typed_and_versioned():
    a = Artifact(id="a1", kind=ArtifactKind.markdown, version=1, body="hello",
                 produced_by_turn_id="t1", at=_utc("2026-04-22T10:00:05"))
    assert a.version == 1
    assert a.kind is ArtifactKind.markdown


def test_signal_carries_source_refs():
    sig = Signal(dimension=Dimension.model_rationale, value=0.8, confidence=0.9,
                 source_refs=("t1","a1"), emitted_by="scorer.rationale",
                 at=_utc("2026-04-22T10:00:06"))
    assert sig.value == 0.8


def test_rubric_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Rubric(version="v0", weights={Dimension.model_rationale: 0.5})


def test_rubric_accepts_five_mvp_dimensions():
    r = Rubric(version="ds_mle@1", weights={
        Dimension.problem_framing: 0.2, Dimension.model_rationale: 0.2,
        Dimension.experiment_design: 0.2, Dimension.insight_interp: 0.2,
        Dimension.communication: 0.2,
    })
    assert sum(r.weights.values()) == pytest.approx(1.0)


def test_score_composite_is_weighted_sum():
    sc = Score(session_id="s1", rubric_version="ds_mle@1",
               per_dimension={Dimension.model_rationale: 0.8,
                              Dimension.communication: 0.6},
               composite=0.7, at=_utc("2026-04-22T10:00:10"))
    assert sc.composite == 0.7
