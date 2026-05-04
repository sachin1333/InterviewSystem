from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from starlette.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.llm_problem_framing_scorer import LlmProblemFramingScorer
from core.domain import ArtifactKind, Dimension
from core.events import ArtifactAttached, Envelope
from core.observability import MetricSink
from core.projections import ArtifactStore, SessionStore
from core.rubric_loader import load_rubric

_RUBRIC = """
name: test
version: 1
dimensions:
  - name: communication
    weight: 1.0
aggregation:
  min_signals_per_dimension: 1
  confidence_weighting: true
"""


def test_scorer_failure_increments_counter(tmp_path: Path) -> None:
    class _FailingProvider:
        def call(self, *, tier: str, prompt: str, stream: bool = False, timeout: float | None = None) -> str:
            raise RuntimeError("simulated scorer failure")

    failing_router = ModelRouter(_FailingProvider(), max_retries=1, sleep=lambda _: None)
    metrics = MetricSink()
    log = SqliteEventLog(tmp_path / "log.db")
    runner = SessionRunner(
        challenger=LlmChallenger(ModelRouter(FakeRouter())),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=tmp_path),
        scorers={Dimension.problem_framing: LlmProblemFramingScorer(failing_router)},
        metric_sink=metrics,
    )

    session_id = "sess-metric-test"
    artifact_id = "art-1"
    env = Envelope(
        session_id=session_id,
        seq=1,
        at=datetime.now(UTC),
        payload=ArtifactAttached(
            id=artifact_id,
            kind=ArtifactKind.markdown,
            produced_by_turn_id="turn-1",
            version=1,
            content="Define the objective metric first.",
        ),
    )
    log.append(env)

    artifacts = ArtifactStore()
    artifacts.apply(env)
    sessions = SessionStore()

    runner._do_scoring(session_id, log, artifact_id, Dimension.problem_framing, sessions, artifacts)

    key = ("scorer_failure_total", (("dimension", "problem_framing"),))
    assert metrics.counters.get(key, 0) == 1


def test_internal_metrics_exposes_chat_counters_without_prompt_text(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    runner = SessionRunner(
        challenger=LlmChallenger(ModelRouter(FakeRouter())),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=tmp_path),
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=tmp_path), follow_redirects=True)
    client.post("/sessions", data={"candidate_handle": "alice", "resume_text": "secret prompt text"})
    response = client.get("/internal/metrics")
    assert response.status_code == 200
    assert "chat_request_ms_count" in response.text
    assert "secret prompt text" not in response.text
