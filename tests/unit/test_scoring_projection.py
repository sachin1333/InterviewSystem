from datetime import UTC, datetime

from core.domain import Dimension, ProblemId, Score
from core.events import (
    Envelope,
    PerProblemScoreComputed,
    ScoreComputed,
    ScoringCompleted,
    ScoringRequested,
)
from core.projections import ScoreStore

UTC = UTC


def _envelope(seq: int, payload: object) -> Envelope:
    return Envelope(
        session_id="s1",
        seq=seq,
        at=datetime(2026, 5, 4, 10, 0, seq, tzinfo=UTC),
        payload=payload,
    )


def test_score_store_tracks_pending_scoring_requests_idempotently():
    store = ScoreStore()
    requested = ScoringRequested(
        problem_id=ProblemId("fraud-risk"),
        artifact_ids=("answer-1",),
        dimensions=(Dimension.model_rationale,),
    )

    store.apply(_envelope(1, requested))
    store.apply(_envelope(2, requested))

    assert store.is_scoring_pending("s1") is True
    assert store.pending_problem_ids("s1") == (ProblemId("fraud-risk"),)
    assert store.completed_problem_ids("s1") == ()


def test_score_store_completion_removes_pending_and_records_completed_idempotently():
    store = ScoreStore()
    requested = ScoringRequested(
        problem_id=ProblemId("fraud-risk"),
        artifact_ids=("answer-1",),
        dimensions=(Dimension.model_rationale,),
    )
    completed = ScoringCompleted(problem_id=ProblemId("fraud-risk"))

    store.apply(_envelope(1, requested))
    store.apply(_envelope(2, completed))
    store.apply(_envelope(3, completed))

    assert store.is_scoring_pending("s1") is False
    assert store.pending_problem_ids("s1") == ()
    assert store.completed_problem_ids("s1") == (ProblemId("fraud-risk"),)


def test_score_store_tracks_pending_and_completed_per_session():
    store = ScoreStore()

    store.apply(_envelope(1, ScoringRequested(
        problem_id=ProblemId("fraud-risk"),
        artifact_ids=("answer-1",),
        dimensions=(Dimension.model_rationale,),
    )))
    store.apply(Envelope(
        session_id="s2",
        seq=1,
        at=datetime(2026, 5, 4, 10, 1, 0, tzinfo=UTC),
        payload=ScoringCompleted(problem_id=ProblemId("forecasting")),
    ))

    assert store.pending_problem_ids("s1") == (ProblemId("fraud-risk"),)
    assert store.completed_problem_ids("s1") == ()
    assert store.pending_problem_ids("s2") == ()
    assert store.completed_problem_ids("s2") == (ProblemId("forecasting"),)


def test_score_store_keeps_existing_score_behaviors_unchanged():
    store = ScoreStore()
    score = Score(
        session_id="s1",
        rubric_version="v1",
        per_dimension={Dimension.communication: 0.7},
        composite=0.7,
        at=datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC),
    )

    store.apply(_envelope(1, ScoreComputed(score=score)))
    store.apply(_envelope(2, PerProblemScoreComputed(
        problem_id=ProblemId("second"),
        ordinal=2,
        score=score,
    )))
    store.apply(_envelope(3, PerProblemScoreComputed(
        problem_id=ProblemId("first"),
        ordinal=1,
        score=score,
    )))

    problem_scores = store.get_problem_scores("s1")
    assert store.get("s1") == score
    assert tuple(entry.problem_id for entry in problem_scores) == (
        ProblemId("first"),
        ProblemId("second"),
    )
