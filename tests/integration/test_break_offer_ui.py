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
from core.domain import Actor, TurnKind
from core.events import CandidateJoined, Envelope, SessionStarted, TurnPosted
from core.pacing import Pacer
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


def test_break_offer_banner_renders_after_threshold(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    sid = "s-break"
    now = datetime.now(UTC)
    log.append(Envelope(session_id=sid, seq=1, at=now, payload=SessionStarted(rubric_version="v", target_answers=10)))
    log.append(Envelope(session_id=sid, seq=2, at=now, payload=CandidateJoined(candidate_handle="a")))
    log.append(Envelope(session_id=sid, seq=3, at=now, payload=TurnPosted(id="q", actor=Actor.challenger, kind=TurnKind.question)))
    log.append(Envelope(session_id=sid, seq=4, at=now, payload=TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)))
    runner = SessionRunner(
        challenger=LlmChallenger(ModelRouter(FakeRouter())),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=tmp_path),
        pacer=Pacer(break_after_answers=1),
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=tmp_path))
    resp = client.get(f"/sessions/{sid}")
    assert "break-offer" in resp.text
