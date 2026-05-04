from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, ProblemId, Score
from core.events import (
    Envelope,
    PerProblemScoreComputed,
    ScoreComputed,
    ScoringCompleted,
    ScoringRequested,
    SessionStarted,
)


def _client(tmp_path: Path) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(tmp_path / "result-progress.db")
    router = ModelRouter(FakeRouter())
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    runner = SessionRunner(challenger=LlmChallenger(router), aggregator=aggregator)
    app = make_app(log=log, runner=runner, output_dir=tmp_path)
    return TestClient(app), log


def _append(log: SqliteEventLog, session_id: str, payload: object) -> None:
    log.append(
        Envelope(
            session_id=session_id,
            seq=(log.last_seq(session_id) or 0) + 1,
            at=datetime.now(UTC),
            payload=payload,
        )
    )


def _score(session_id: str, composite: float = 0.72) -> Score:
    return Score(
        session_id=session_id,
        rubric_version="ds-ml-engineer@1",
        per_dimension={Dimension.communication: composite},
        composite=composite,
        at=datetime.now(UTC),
    )


def test_result_page_shows_pending_scoring_state_and_context(tmp_path: Path) -> None:
    client, log = _client(tmp_path)
    session_id = "s-result-pending"
    _append(log, session_id, SessionStarted(rubric_version="ds-ml-engineer@1"))
    _append(
        log,
        session_id,
        ScoringRequested(
            problem_id=ProblemId("fraud-risk"),
            artifact_ids=("answer-1",),
            dimensions=(Dimension.communication,),
        ),
    )

    response = client.get(f"/sessions/{session_id}/result")

    assert response.status_code == 200
    assert response.context["scoring_pending"] is True
    assert response.context["completed_problem_ids"] == ()
    assert "Scoring in progress" in response.text
    assert "Score not yet computed." not in response.text


def test_result_page_shows_partial_scores_until_final_score_exists(tmp_path: Path) -> None:
    client, log = _client(tmp_path)
    session_id = "s-result-partial"
    _append(log, session_id, SessionStarted(rubric_version="ds-ml-engineer@1"))
    _append(
        log,
        session_id,
        ScoringRequested(
            problem_id=ProblemId("fraud-risk"),
            artifact_ids=("answer-1",),
            dimensions=(Dimension.communication,),
        ),
    )
    _append(log, session_id, PerProblemScoreComputed(problem_id=ProblemId("fraud-risk"), ordinal=1, score=_score(session_id, 0.65)))
    _append(log, session_id, ScoringCompleted(problem_id=ProblemId("fraud-risk")))

    response = client.get(f"/sessions/{session_id}/result")

    assert response.status_code == 200
    assert response.context["scoring_pending"] is False
    assert response.context["completed_problem_ids"] == (ProblemId("fraud-risk"),)
    assert "Partial score" in response.text
    assert "Per-problem breakdown" in response.text
    assert "Problem 1" in response.text
    assert "0.65" in response.text
    assert "Composite score:" not in response.text
    assert "Score not yet computed." not in response.text


def test_result_page_shows_final_composite_score_when_score_computed(tmp_path: Path) -> None:
    client, log = _client(tmp_path)
    session_id = "s-result-final"
    _append(log, session_id, SessionStarted(rubric_version="ds-ml-engineer@1"))
    _append(log, session_id, PerProblemScoreComputed(problem_id=ProblemId("fraud-risk"), ordinal=1, score=_score(session_id, 0.70)))
    _append(log, session_id, ScoreComputed(score=_score(session_id, 0.82)))

    response = client.get(f"/sessions/{session_id}/result")

    assert response.status_code == 200
    assert response.context["scoring_pending"] is False
    assert response.context["completed_problem_ids"] == ()
    assert "Composite score: 0.82" in response.text
    assert "Rubric: ds-ml-engineer@1" in response.text
    assert "Partial score" not in response.text
    assert "Scoring in progress" not in response.text
