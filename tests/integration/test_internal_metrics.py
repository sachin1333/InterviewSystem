from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.observability import MetricSink
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


def test_metric_sink_exports_latency_and_scoring_metrics_without_raw_candidate_text() -> None:
    sink = MetricSink()

    sink.observe_chat_request(route="/sessions/{session_id}/turn", elapsed_ms=42.5)
    sink.observe_llm_call(component="examiner", elapsed_ms=125.25, outcome="ok")
    sink.set_scoring_queue_depth(3)
    sink.observe_scoring_job(elapsed_ms=250.0, outcome="ok")
    sink.increment_structured_output_failure(component="problem_scorer", reason="schema_validation")
    sink.observe(
        "llm_call_ms",
        99.0,
        labels={
            "component": "challenger",
            "prompt_text": "raw prompt secret",
            "resume": "raw resume secret",
            "candidate": "alice secret",
        },
    )

    text = sink.export_prometheus()

    assert 'chat_request_ms_count{route="/sessions/{session_id}/turn"} 1' in text
    assert 'llm_call_ms_count{component="examiner",outcome="ok"} 1' in text
    assert "scoring_queue_depth 3" in text
    assert 'scoring_job_ms_count{outcome="ok"} 1' in text
    assert 'structured_output_failures_total{component="problem_scorer",reason="schema_validation"} 1' in text
    assert 'llm_call_ms_count{component="challenger"} 1' in text
    assert "raw prompt secret" not in text
    assert "raw resume secret" not in text
    assert "alice secret" not in text


def test_chat_metrics_normalize_session_ids(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    runner = SessionRunner(
        challenger=LlmChallenger(ModelRouter(FakeRouter())),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=tmp_path),
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=tmp_path), follow_redirects=True)
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = str(created.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    client.get(f"/sessions/{session_id}/result")

    metrics = client.get("/internal/metrics").text

    assert session_id not in metrics
    assert 'chat_request_ms_count{route="/sessions/{session_id}/result"}' in metrics
