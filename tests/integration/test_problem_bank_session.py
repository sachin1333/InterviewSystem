from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import ExaminerOutcome
from adapters.http.app import create_app, make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer._base import ScorerResult
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Artifact, Dimension, Problem, ProblemId
from core.events import (
    CandidateJoined,
    Envelope,
    ProblemClosed,
    ProblemIntroduced,
    ScoreComputed,
    SessionEnded,
    SessionStarted,
)
from core.problem_bank import ProblemBank
from core.session_boot import replay


def _aggregator(tmp_path: Path) -> RubricAggregator:
    return RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )


def _append_started(log: SqliteEventLog, session_id: str) -> None:
    now = datetime.now(UTC)
    log.append(Envelope(
        session_id=session_id,
        seq=1,
        at=now,
        payload=SessionStarted(
            rubric_version="ds-ml-engineer@1",
            target_answers=1,
            max_probes_per_prompt=0,
            pack_id="ds-ml-v1",
        ),
    ))
    log.append(Envelope(
        session_id=session_id,
        seq=2,
        at=now,
        payload=CandidateJoined(candidate_handle="alice"),
    ))


def test_session_runner_draws_problem_sequence_from_bank(tmp_path: Path) -> None:
    bank = ProblemBank.from_yaml(Path("templates/problem_banks/ds-ml-engineer-v1.yaml"))
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=_aggregator(tmp_path),
        scored_dimensions=(
            Dimension.problem_framing,
            Dimension.model_rationale,
            Dimension.experiment_design,
            Dimension.insight_interp,
            Dimension.communication,
        ),
        problem_bank=bank,
    )
    session_id = "sess-bank-001"
    _append_started(log, session_id)

    result = runner.advance(session_id, log)

    expected_first = bank.pick_sequence(session_id)[0]
    assert result.state == "await_input"
    assert result.display_text == expected_first.opener_text
    events = [env.payload for env in log.get_session(session_id)]
    introduced = [event for event in events if isinstance(event, ProblemIntroduced)]
    assert introduced[0].problem_id == expected_first.id
    assert introduced[0].ordinal == 1
    sessions, _, _, _, _ = replay(session_id, log)
    record = sessions.get(session_id)
    assert record is not None
    assert record["current_problem_id"] == expected_first.id


def test_create_app_loads_problem_bank_for_new_sessions(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FAKE_LLM", "1")
    app = create_app(db_path=str(tmp_path / "app.db"))

    runner: SessionRunner = app.state.runner

    assert runner.problem_bank is not None
    assert len(runner.problem_bank) >= 4
    assert runner.problem_bank.prewarm_openers()[0]
    assert runner.examiner is not None


def test_create_app_chat_rubric_matches_scored_dimensions(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FAKE_LLM", "1")
    app = create_app(db_path=str(tmp_path / "app.db"))
    runner: SessionRunner = app.state.runner

    assert set(runner.scored_dimensions) == set(runner.aggregator.rubric.weights)
    assert set(runner.scorers) == set(runner.scored_dimensions)
    assert Dimension.experiment_design in runner.scored_dimensions
    assert Dimension.response_authenticity not in runner.scored_dimensions


class _ClosingExaminer:
    def review(self, session_id: str, recent_turns: list[Any], **kwargs: Any) -> tuple[ExaminerOutcome, None]:
        del session_id, recent_turns, kwargs
        return ExaminerOutcome(
            action="close",
            ok_to_advance=True,
            close_reason="coverage_saturated",
            rationale="enough signal",
        ), None


class _ExplodingScorer:
    def score_optional(self, session_id: str, artifact: Artifact) -> ScorerResult:
        del session_id, artifact
        raise AssertionError("scoring must not run while a problem is still active")


def test_problem_bank_answer_closes_problem_and_advances_without_premature_scoring(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    problems = [
        Problem(id=ProblemId("p1"), opener_text="First opener"),
        Problem(id=ProblemId("p2"), opener_text="Second opener"),
    ]
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=_ClosingExaminer(),  # type: ignore[arg-type]
        scorers={Dimension.problem_framing: _ExplodingScorer()},  # type: ignore[dict-item]
        scored_dimensions=(Dimension.problem_framing,),
        aggregator=_aggregator(tmp_path),
        problems=problems,
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, max_probes=0)
    client = TestClient(app, raise_server_exceptions=True)

    created = client.post("/sessions", data={"candidate_handle": "alice"}, follow_redirects=True)
    session_id = created.url.path.rsplit("/", 1)[-1]
    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would clarify the business metric.", "turn_nonce": "n1"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "/result" not in str(response.url)
    assert "Second opener" in response.text

    events = [env.payload for env in log.get_session(session_id)]
    assert len([event for event in events if isinstance(event, ProblemClosed)]) == 1
    assert len([event for event in events if isinstance(event, ProblemIntroduced)]) == 2
    assert not any(isinstance(event, ScoreComputed) for event in events)
    assert not any(isinstance(event, SessionEnded) for event in events)

    sessions, _, _, _, _ = replay(session_id, log)
    record = sessions.get(session_id)
    assert record is not None
    assert record["current_problem_id"] == ProblemId("p2")
