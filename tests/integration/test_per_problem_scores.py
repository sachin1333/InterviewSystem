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
from core.domain import Actor, ArtifactKind, Dimension, ProblemId, Signal, TurnKind
from core.events import (
    ArtifactAttached,
    CandidateJoined,
    Envelope,
    PerProblemScoreComputed,
    ProblemClosed,
    ProblemIntroduced,
    SessionStarted,
    SignalEmitted,
    TurnPosted,
)
from core.session_boot import replay


def _runner(tmp_path: Path) -> SessionRunner:
    router = ModelRouter(FakeRouter())
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    return SessionRunner(challenger=LlmChallenger(router), aggregator=aggregator)


def _append(log: SqliteEventLog, session_id: str, payload: object) -> None:
    seq = (log.last_seq(session_id) or 0) + 1
    log.append(Envelope(session_id=session_id, seq=seq, at=datetime.now(UTC), payload=payload))


def _signal(dim: Dimension, value: float, artifact_id: str) -> Signal:
    return Signal(
        dimension=dim,
        value=value,
        confidence=1.0,
        source_refs=(artifact_id,),
        emitted_by="test",
        at=datetime.now(UTC),
    )


def _seed_two_problem_scores(log: SqliteEventLog, session_id: str) -> None:
    _append(log, session_id, SessionStarted(rubric_version="ds"))
    _append(log, session_id, CandidateJoined(candidate_handle="alice"))
    _append(log, session_id, ProblemIntroduced(problem_id=ProblemId("p1"), opener_text="P1", ordinal=1))
    _append(log, session_id, TurnPosted(id="t-a1", actor=Actor.candidate, kind=TurnKind.answer))
    _append(log, session_id, ArtifactAttached(id="a1", kind=ArtifactKind.markdown, produced_by_turn_id="t-a1", version=1, content="answer one"))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.problem_framing, 0.8, "a1")))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.communication, 0.6, "a1")))
    _append(log, session_id, ProblemClosed(problem_id=ProblemId("p1"), reason="coverage_saturated", rationale="enough"))
    _append(log, session_id, ProblemIntroduced(problem_id=ProblemId("p2"), opener_text="P2", ordinal=2))
    _append(log, session_id, TurnPosted(id="t-a2", actor=Actor.candidate, kind=TurnKind.answer))
    _append(log, session_id, ArtifactAttached(id="a2", kind=ArtifactKind.markdown, produced_by_turn_id="t-a2", version=1, content="answer two"))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.model_rationale, 0.4, "a2")))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.insight_interp, 0.9, "a2")))
    _append(log, session_id, ProblemClosed(problem_id=ProblemId("p2"), reason="coverage_saturated", rationale="enough"))


def test_aggregate_emits_per_problem_score_events_and_projection(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "scores.db")
    session_id = "s-problem-scores"
    _seed_two_problem_scores(log, session_id)
    runner = _runner(tmp_path)

    runner._do_aggregate(session_id, log)

    events = [env.payload for env in log.get_session(session_id)]
    per_problem_events = [event for event in events if isinstance(event, PerProblemScoreComputed)]
    assert [event.problem_id for event in per_problem_events] == [ProblemId("p1"), ProblemId("p2")]
    assert [event.ordinal for event in per_problem_events] == [1, 2]
    assert per_problem_events[0].score.per_dimension[Dimension.problem_framing] == 0.8
    assert per_problem_events[0].score.per_dimension[Dimension.communication] == 0.6
    assert per_problem_events[1].score.per_dimension[Dimension.model_rationale] == 0.4
    assert per_problem_events[1].score.per_dimension[Dimension.insight_interp] == 0.9

    _, scores, _, _, _ = replay(session_id, log)
    projected = scores.get_problem_scores(session_id)
    assert [entry.problem_id for entry in projected] == [ProblemId("p1"), ProblemId("p2")]


def test_result_page_renders_per_problem_breakdown(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "scores.db")
    session_id = "s-dashboard"
    _seed_two_problem_scores(log, session_id)
    runner = _runner(tmp_path)
    runner._do_aggregate(session_id, log)
    app = make_app(log=log, runner=runner, output_dir=tmp_path)
    client = TestClient(app)

    response = client.get(f"/sessions/{session_id}/result")

    assert response.status_code == 200
    assert "Per-problem breakdown" in response.text
    assert "Problem 1" in response.text
    assert "Problem 2" in response.text
    assert "problem_framing" in response.text
    assert "model_rationale" in response.text


def test_legacy_stage_session_aggregate_score_stays_stable(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "legacy.db")
    session_id = "s-legacy-score-regression"
    _append(log, session_id, SessionStarted(rubric_version="ds"))
    _append(log, session_id, CandidateJoined(candidate_handle="legacy"))
    _append(log, session_id, TurnPosted(id="t-a", actor=Actor.candidate, kind=TurnKind.answer))
    _append(log, session_id, ArtifactAttached(id="a", kind=ArtifactKind.markdown, produced_by_turn_id="t-a", version=1, content="legacy answer"))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.problem_framing, 0.70, "a")))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.model_rationale, 0.60, "a")))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.insight_interp, 0.50, "a")))
    _append(log, session_id, SignalEmitted(signal=_signal(Dimension.communication, 0.80, "a")))
    runner = _runner(tmp_path)

    runner._do_aggregate(session_id, log)

    _, scores, _, _, _ = replay(session_id, log)
    score = scores.get(session_id)
    assert score is not None
    assert score.per_dimension[Dimension.problem_framing] == 0.70
    assert score.per_dimension[Dimension.model_rationale] == 0.60
    assert score.per_dimension[Dimension.insight_interp] == 0.50
    assert score.per_dimension[Dimension.communication] == 0.80
    assert scores.get_problem_scores(session_id) == ()
