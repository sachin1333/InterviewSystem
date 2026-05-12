from __future__ import annotations

import hashlib
import queue
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from core.contracts import EventLog
from core.domain import Artifact, Dimension, ProblemId, Signal
from core.events import Envelope, ScorerFailed, ScoringCompleted, SignalEmitted
from core.observability import MetricSink

ScoringEvent = SignalEmitted | ScorerFailed


@dataclass(frozen=True)
class ScoringJob:
    session_id: str
    problem_id: ProblemId
    artifact_ids: tuple[str, ...]
    dimensions: tuple[Dimension, ...]
    problem_context: str = ""
    candidate_artifacts: tuple[Artifact, ...] = ()


class ProblemScorer(Protocol):
    def score_problem(self, job: ScoringJob) -> Iterable[ScoringEvent]: ...


class ProblemScoringResultLike(Protocol):
    @property
    def signals(self) -> tuple[Signal, ...]: ...

    @property
    def failures(self) -> tuple[ScorerFailed, ...]: ...


class ProblemLevelScorer(Protocol):
    def score_problem(
        self,
        *,
        session_id: str,
        problem_id: ProblemId,
        problem_context: str,
        candidate_artifacts: tuple[Artifact, ...],
        dimensions: tuple[Dimension, ...],
    ) -> ProblemScoringResultLike: ...


class ProblemScoringJobAdapter:
    """Adapts the problem-level scorer API to background-worker jobs."""

    def __init__(self, scorer: ProblemLevelScorer) -> None:
        self._scorer = scorer

    def score_problem(self, job: ScoringJob) -> Iterable[ScoringEvent]:
        result = self._scorer.score_problem(
            session_id=job.session_id,
            problem_id=job.problem_id,
            problem_context=job.problem_context,
            candidate_artifacts=job.candidate_artifacts,
            dimensions=job.dimensions,
        )
        return (
            *(SignalEmitted(signal=signal) for signal in result.signals),
            *result.failures,
        )


class BackgroundScoringWorker:
    """Minimal in-process worker for problem-scoped scoring jobs."""

    def __init__(
        self,
        *,
        log: EventLog,
        scorer: ProblemScorer,
        metrics: MetricSink | None = None,
    ) -> None:
        self._log = log
        self._scorer = scorer
        self._queue: queue.Queue[ScoringJob | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._job_lock = threading.Lock()
        self._queued_or_done: set[tuple[str, ProblemId]] = set()
        self._metrics = metrics

    def enqueue(self, job: ScoringJob) -> None:
        """Queue scoring work without waiting for the scorer."""
        job_key = (job.session_id, job.problem_id)
        with self._job_lock:
            if job_key in self._queued_or_done:
                return
            self._queued_or_done.add(job_key)
        self._queue.put_nowait(job)
        self._record_queue_depth()

    def start(self) -> None:
        """Start the background thread; safe to call more than once."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="background-scoring-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the worker to drain already-queued jobs and wait up to timeout seconds."""
        thread = self._thread
        if thread is None:
            return
        self._queue.put_nowait(None)
        thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._record_queue_depth()
                if job is None:
                    return
                self._process(job)
            finally:
                self._queue.task_done()
                self._record_queue_depth()

    def _process(self, job: ScoringJob) -> None:
        started = time.monotonic()
        outcome = "ok"
        try:
            events = tuple(self._scorer.score_problem(job))
        except Exception as exc:
            outcome = "error"
            reason = str(exc) or exc.__class__.__name__
            events = tuple(
                ScorerFailed(dimension=dimension, reason=reason)
                for dimension in job.dimensions
            )
        if any(isinstance(event, ScorerFailed) for event in events):
            outcome = "error"

        for index, event in enumerate(events):
            if isinstance(event, (SignalEmitted, ScorerFailed)):
                self._append(job, event, self._idem_key_for_event(job, event, index))
        self._append(
            job,
            ScoringCompleted(problem_id=job.problem_id),
            f"scoring-completed:{job.problem_id}",
        )
        if self._metrics is not None:
            self._metrics.observe_scoring_job(
                elapsed_ms=(time.monotonic() - started) * 1000,
                outcome=outcome,
            )

    def _record_queue_depth(self) -> None:
        if self._metrics is not None:
            self._metrics.set_scoring_queue_depth(self._queue.qsize())

    def _append(self, job: ScoringJob, payload: object, idem_key: str) -> Envelope:
        """Append with a fresh sequence number and retry benign sequence races."""
        for _ in range(3):
            envelope = Envelope(
                session_id=job.session_id,
                seq=(self._log.last_seq(job.session_id) or 0) + 1,
                at=datetime.now(UTC),
                payload=payload,
                idem_key=idem_key,
            )
            try:
                return self._log.append(envelope, idem_key)
            except ValueError:
                # Another writer may have advanced seq between last_seq and append.
                # Retry with a fresh seq; idempotency still handles duplicates.
                continue
        envelope = Envelope(
            session_id=job.session_id,
            seq=(self._log.last_seq(job.session_id) or 0) + 1,
            at=datetime.now(UTC),
            payload=payload,
            idem_key=idem_key,
        )
        return self._log.append(envelope, idem_key)

    @staticmethod
    def _idem_key_for_event(job: ScoringJob, event: ScoringEvent, index: int) -> str:
        if isinstance(event, SignalEmitted):
            signal_id = event.signal.id or f"{event.signal.dimension.value}:{index}"
            return f"scoring-signal:{job.problem_id}:{signal_id}"
        reason_key = hashlib.sha256(event.reason.encode("utf8")).hexdigest()[:16]
        return f"scoring-failed:{job.problem_id}:{event.dimension.value}:{reason_key}"
