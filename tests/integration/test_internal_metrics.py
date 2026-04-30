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
