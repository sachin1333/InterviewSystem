from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import ExaminerOutcome
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.background_worker import BackgroundScoringWorker, ScoringJob
from core.domain import Dimension, Problem, ProblemId, Signal
from core.events import ScoringCompleted, ScoringRequested, SignalEmitted


class _ClosingExaminer:
    def review(
        self, session_id: str, recent_turns: list[Any], **kwargs: Any
    ) -> tuple[ExaminerOutcome, None]:
        del session_id, recent_turns, kwargs
        return ExaminerOutcome(
            action="close",
            ok_to_advance=True,
            close_reason="coverage_saturated",
            rationale="enough signal",
        ), None


class _SlowProblemScorer:
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.entered = Event()
        self.calls: list[ScoringJob] = []

    def score_problem(self, job: ScoringJob) -> Iterable[SignalEmitted]:
        self.calls.append(job)
        self.entered.set()
        time.sleep(self.delay_s)
        return (
            SignalEmitted(
                signal=Signal(
                    dimension=job.dimensions[0],
                    value=0.7,
                    confidence=0.8,
                    source_refs=job.artifact_ids,
                    emitted_by="problem_scorer:test",
                    justification="slow fake done",
                    at=datetime.now(UTC),
                )
            ),
        )


def test_problem_close_enqueues_scoring_without_blocking_candidate_get(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    slow_scorer = _SlowProblemScorer(delay_s=1.2)
    worker = BackgroundScoringWorker(log=log, scorer=slow_scorer)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=_ClosingExaminer(),  # type: ignore[arg-type]
        scored_dimensions=(Dimension.problem_framing,),
        aggregator=RubricAggregator.from_yaml(
            Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path
        ),
        problems=(Problem(id=ProblemId("p1"), opener_text="First opener"),),
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, max_probes=0, scoring_worker=worker)

    with TestClient(app, raise_server_exceptions=True) as client:
        created = client.post("/sessions", data={"candidate_handle": "alice"}, follow_redirects=True)
        session_id = created.url.path.rsplit("/", 1)[-1]

        started = time.monotonic()
        response = client.post(
            f"/sessions/{session_id}/turn",
            data={
                "answer": "I would define the target metric and candidate cohort.",
                "turn_nonce": "n1",
            },
            follow_redirects=True,
        )
        elapsed = time.monotonic() - started

        assert response.status_code == 200
        assert elapsed < 3.0
        assert slow_scorer.entered.wait(timeout=1.0)

        payloads = [env.payload for env in log.get_session(session_id)]
        assert any(isinstance(payload, ScoringRequested) for payload in payloads)
        requested = next(payload for payload in payloads if isinstance(payload, ScoringRequested))
        assert requested.problem_id == ProblemId("p1")
        assert requested.dimensions == (Dimension.problem_framing,)
        assert slow_scorer.calls[0].artifact_ids
        assert not any(isinstance(payload, ScoringCompleted) for payload in payloads)

    payloads_after_stop = [env.payload for env in log.get_session(session_id)]
    assert any(isinstance(payload, ScoringCompleted) for payload in payloads_after_stop)

    with TestClient(app, raise_server_exceptions=True) as client:
        result = client.get(f"/sessions/{session_id}/result")

    assert result.status_code == 200
    assert "Composite score" in result.text


class _ImmediateProblemScorer:
    def __init__(self) -> None:
        self.calls: list[ScoringJob] = []

    def score_problem(self, job: ScoringJob) -> Iterable[SignalEmitted]:
        self.calls.append(job)
        return (
            SignalEmitted(
                signal=Signal(
                    dimension=job.dimensions[0],
                    value=0.8,
                    confidence=0.9,
                    source_refs=job.artifact_ids,
                    emitted_by="problem_scorer:test",
                    justification="immediate fake done",
                    at=datetime.now(UTC),
                )
            ),
        )


def test_async_problem_scoring_finalizes_from_completed_problem_scoped_dimensions(
    tmp_path: Path,
) -> None:
    log = SqliteEventLog(tmp_path / "problem-bank.db")
    router = ModelRouter(FakeRouter())
    scorer = _ImmediateProblemScorer()
    worker = BackgroundScoringWorker(log=log, scorer=scorer)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=_ClosingExaminer(),  # type: ignore[arg-type]
        scored_dimensions=(Dimension.problem_framing, Dimension.model_rationale),
        aggregator=RubricAggregator.from_yaml(
            Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path
        ),
        problems=(
            Problem(
                id=ProblemId("p1"),
                opener_text="First opener",
                target_dimensions=(Dimension.problem_framing,),
            ),
            Problem(
                id=ProblemId("p2"),
                opener_text="Second opener",
                target_dimensions=(Dimension.model_rationale,),
            ),
        ),
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, max_probes=0, scoring_worker=worker)

    with TestClient(app, raise_server_exceptions=True) as client:
        created = client.post("/sessions", data={"candidate_handle": "alice"}, follow_redirects=True)
        session_id = created.url.path.rsplit("/", 1)[-1]
        client.post(
            f"/sessions/{session_id}/turn",
            data={"answer": "I would define the metric.", "turn_nonce": "p1"},
            follow_redirects=True,
        )
        client.post(
            f"/sessions/{session_id}/turn",
            data={"answer": "I would choose a baseline because it is interpretable.", "turn_nonce": "p2"},
            follow_redirects=True,
        )

    with TestClient(app, raise_server_exceptions=True) as client:
        result = client.get(f"/sessions/{session_id}/result")

    assert result.status_code == 200
    assert "Composite score" in result.text


def test_result_route_recovers_pending_scoring_request_after_worker_restart(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "restart.db")
    router = ModelRouter(FakeRouter())
    first_scorer = _SlowProblemScorer(delay_s=10.0)
    first_worker = BackgroundScoringWorker(log=log, scorer=first_scorer)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=_ClosingExaminer(),  # type: ignore[arg-type]
        scored_dimensions=(Dimension.problem_framing,),
        aggregator=RubricAggregator.from_yaml(
            Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path
        ),
        problems=(Problem(id=ProblemId("p1"), opener_text="First opener"),),
    )
    first_app = make_app(
        log=log,
        runner=runner,
        output_dir=tmp_path,
        max_probes=0,
        scoring_worker=first_worker,
    )
    with TestClient(first_app, raise_server_exceptions=True) as client:
        created = client.post("/sessions", data={"candidate_handle": "alice"}, follow_redirects=True)
        session_id = created.url.path.rsplit("/", 1)[-1]
        client.post(
            f"/sessions/{session_id}/turn",
            data={"answer": "I would define the target metric.", "turn_nonce": "n1"},
            follow_redirects=True,
        )
        assert first_scorer.entered.wait(timeout=1.0)

    assert any(isinstance(env.payload, ScoringRequested) for env in log.get_session(session_id))
    assert not any(isinstance(env.payload, ScoringCompleted) for env in log.get_session(session_id))

    recovered_scorer = _ImmediateProblemScorer()
    recovered_worker = BackgroundScoringWorker(log=log, scorer=recovered_scorer)
    runner.scoring_worker = None
    recovered_app = make_app(
        log=log,
        runner=runner,
        output_dir=tmp_path,
        max_probes=0,
        scoring_worker=recovered_worker,
    )

    with TestClient(recovered_app, raise_server_exceptions=True) as client:
        pending_result = client.get(f"/sessions/{session_id}/result")

    assert recovered_scorer.calls
    assert pending_result.status_code == 200

    with TestClient(recovered_app, raise_server_exceptions=True) as client:
        result = client.get(f"/sessions/{session_id}/result")

    assert result.status_code == 200
    assert "Composite score" in result.text
