from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, cast

from starlette.testclient import TestClient

from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.scorer.background_worker import BackgroundScoringWorker, ScoringJob
from core.contracts import EventLog
from core.domain import Dimension, ProblemId, Signal
from core.eventlog import InMemoryEventLog
from core.events import ScorerFailed, ScoringCompleted, SignalEmitted
from core.observability import MetricSink


class FakeProblemScorer:
    def __init__(self, events: Iterable[SignalEmitted | ScorerFailed] = ()) -> None:
        self.events = tuple(events)
        self.calls: list[ScoringJob] = []

    def score_problem(self, job: ScoringJob) -> Iterable[SignalEmitted | ScorerFailed]:
        self.calls.append(job)
        return self.events


class FailingProblemScorer:
    def score_problem(self, job: ScoringJob) -> Iterable[SignalEmitted | ScorerFailed]:
        raise RuntimeError("scorer backend unavailable")


class BlockingProblemScorer:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def score_problem(self, job: ScoringJob) -> Iterable[SignalEmitted | ScorerFailed]:
        self.entered.set()
        self.release.wait(timeout=1.0)
        return ()


class LifecycleWorker:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self, timeout: float = 2.0) -> None:
        self.stopped += 1


def _signal(dimension: Dimension = Dimension.communication) -> Signal:
    return Signal(
        id=f"sig-a1-{dimension.value}",
        dimension=dimension,
        value=0.8,
        confidence=0.9,
        source_refs=("artifact://a1",),
        emitted_by="problem_scorer:test",
        justification="clear explanation",
        at=datetime.now(UTC),
    )


def _job(dimensions: tuple[Dimension, ...] = (Dimension.communication,)) -> ScoringJob:
    return ScoringJob(
        session_id="s1",
        problem_id=ProblemId("p1"),
        artifact_ids=("a1",),
        dimensions=dimensions,
    )


def _payloads(log: EventLog) -> list[object]:
    return [env.payload for env in log.get_session("s1")]


def test_enqueue_returns_immediately_while_scorer_runs_in_background() -> None:
    log = InMemoryEventLog()
    scorer = BlockingProblemScorer()
    worker = BackgroundScoringWorker(log=log, scorer=scorer)
    worker.start()

    started = time.monotonic()
    worker.enqueue(_job())
    elapsed = time.monotonic() - started

    try:
        assert elapsed < 0.05
        assert scorer.entered.wait(timeout=0.5)
        assert not any(isinstance(payload, ScoringCompleted) for payload in _payloads(log))
    finally:
        scorer.release.set()
        worker.stop(timeout=2.0)


def test_worker_appends_returned_signal_failure_and_completion_events_idempotently() -> None:
    signal_event = SignalEmitted(signal=_signal())
    failure_event = ScorerFailed(dimension=Dimension.model_rationale, reason="low confidence")
    log = InMemoryEventLog()
    scorer = FakeProblemScorer((signal_event, failure_event))
    worker = BackgroundScoringWorker(log=log, scorer=scorer)
    worker.start()

    worker.enqueue(_job((Dimension.communication, Dimension.model_rationale)))
    worker.enqueue(_job((Dimension.communication, Dimension.model_rationale)))
    worker.stop(timeout=2.0)

    payloads = _payloads(log)
    assert len(scorer.calls) == 1
    assert [type(payload) for payload in payloads] == [SignalEmitted, ScorerFailed, ScoringCompleted]
    assert cast(SignalEmitted, payloads[0]).signal == signal_event.signal
    assert cast(ScorerFailed, payloads[1]).dimension is Dimension.model_rationale
    assert cast(ScoringCompleted, payloads[2]).problem_id == ProblemId("p1")


def test_worker_records_failure_for_each_requested_dimension_then_completes() -> None:
    dimensions = (Dimension.communication, Dimension.experiment_design)
    log = InMemoryEventLog()
    worker = BackgroundScoringWorker(log=log, scorer=FailingProblemScorer())
    worker.start()

    worker.enqueue(_job(dimensions))
    worker.stop(timeout=2.0)

    payloads = _payloads(log)
    failures = [payload for payload in payloads if isinstance(payload, ScorerFailed)]
    assert [failure.dimension for failure in failures] == list(dimensions)
    assert all("scorer backend unavailable" in failure.reason for failure in failures)
    assert isinstance(payloads[-1], ScoringCompleted)


def test_stop_drains_queued_jobs_without_corrupting_event_sequence() -> None:
    log = InMemoryEventLog()
    worker = BackgroundScoringWorker(log=log, scorer=FakeProblemScorer((SignalEmitted(signal=_signal()),)))
    worker.start()

    for problem_id in ("p1", "p2", "p3"):
        worker.enqueue(ScoringJob(
            session_id="s1",
            problem_id=ProblemId(problem_id),
            artifact_ids=("a1",),
            dimensions=(Dimension.communication,),
        ))
    worker.stop(timeout=2.0)

    events = log.get_session("s1")
    assert [env.seq for env in events] == list(range(1, len(events) + 1))
    completed = [env.payload for env in events if isinstance(env.payload, ScoringCompleted)]
    assert [payload.problem_id for payload in completed] == [ProblemId("p1"), ProblemId("p2"), ProblemId("p3")]


def test_worker_records_queue_depth_and_job_duration_metrics() -> None:
    metrics = MetricSink()
    log = InMemoryEventLog()
    worker = BackgroundScoringWorker(
        log=log,
        scorer=FakeProblemScorer((SignalEmitted(signal=_signal()),)),
        metrics=metrics,
    )
    worker.start()

    worker.enqueue(_job())
    worker.stop(timeout=2.0)

    exported = metrics.export_prometheus()
    assert "scoring_queue_depth 0" in exported
    assert 'scoring_job_ms_count{outcome="ok"} 1' in exported


def test_make_app_starts_and_stops_provided_scoring_worker(tmp_path: Path) -> None:
    log = InMemoryEventLog()
    lifecycle_worker = LifecycleWorker()

    app = make_app(
        log=log,
        runner=cast(SessionRunner, _MinimalRunner()),
        output_dir=tmp_path,
        scoring_worker=cast(Any, lifecycle_worker),
    )

    assert app.state.scoring_worker is lifecycle_worker
    with TestClient(app):
        assert lifecycle_worker.started == 1
        assert lifecycle_worker.stopped == 0
    assert lifecycle_worker.stopped == 1


class _MinimalRunner:
    def __init__(self) -> None:
        self.session_workspace_root = Path("outputs") / "sessions"
        self.problems: list[Any] = []
        self.problem_bank = None
        self.examiner = None
