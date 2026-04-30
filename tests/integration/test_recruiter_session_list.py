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
from core.events import Envelope, SessionStarted
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


def test_recruiter_sessions_lists_sessions(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    log.append(Envelope(session_id="s-list", seq=1, at=datetime.now(UTC), payload=SessionStarted(rubric_version="v")))
    runner = SessionRunner(challenger=LlmChallenger(ModelRouter(FakeRouter())), aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=tmp_path))
    client = TestClient(make_app(log=log, runner=runner, output_dir=tmp_path))
    resp = client.get("/recruiter/sessions")
    assert resp.status_code == 200
    assert "/recruiter/sessions/s-list" in resp.text
