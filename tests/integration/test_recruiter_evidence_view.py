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
from core.domain import Actor, ArtifactKind, Dimension, Score, Signal, TurnKind
from core.events import (
    ArtifactAttached,
    Envelope,
    ScoreComputed,
    SessionStarted,
    SignalEmitted,
    TurnPosted,
)
from core.rubric_loader import load_rubric

_RUBRIC = """
name: test
version: 1
dimensions:
  - name: model_rationale
    weight: 1.0
aggregation:
  min_signals_per_dimension: 1
  confidence_weighting: true
"""


def _make_client(tmp_path: Path) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    rubric = load_rubric(yaml_str=_RUBRIC)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=RubricAggregator(rubric, output_dir=tmp_path / "outputs"),
        scored_dimensions=(Dimension.model_rationale,),
    )
    return TestClient(make_app(log=log, runner=runner, output_dir=tmp_path / "outputs")), log


def _seed_scored_session(log: SqliteEventLog, session_id: str = "sess-evidence") -> None:
    now = datetime.now(UTC)
    log.append(Envelope(session_id=session_id, seq=1, at=now, payload=SessionStarted(rubric_version="v1")))
    log.append(Envelope(session_id=session_id, seq=2, at=now, payload=TurnPosted(id="turn-1", actor=Actor.candidate, kind=TurnKind.answer)))
    log.append(Envelope(session_id=session_id, seq=3, at=now, payload=ArtifactAttached(id="artifact-1", kind=ArtifactKind.markdown, produced_by_turn_id="turn-1", version=1, content="answer")))
    signal = Signal(
        id="sig-1",
        dimension=Dimension.model_rationale,
        value=0.8,
        confidence=0.9,
        source_refs=("artifact-1",),
        emitted_by="rationale_scorer",
        justification="Candidate compared nonlinear model against a simpler baseline.",
        at=now,
    )
    log.append(Envelope(session_id=session_id, seq=4, at=now, payload=SignalEmitted(signal=signal)))
    log.append(Envelope(session_id=session_id, seq=5, at=now, payload=ScoreComputed(score=Score(session_id=session_id, rubric_version="v1", per_dimension={Dimension.model_rationale: 0.8}, composite=0.8, at=now))))


def test_recruiter_session_shows_score_signal_justification_and_sources(tmp_path: Path) -> None:
    client, log = _make_client(tmp_path)
    _seed_scored_session(log)

    response = client.get("/recruiter/sessions/sess-evidence")

    assert response.status_code == 200
    assert "Composite score" in response.text
    assert "model_rationale" in response.text
    assert "Candidate compared nonlinear model" in response.text
    assert "artifact-1" in response.text


def test_recruiter_can_append_human_override(tmp_path: Path) -> None:
    client, log = _make_client(tmp_path)
    _seed_scored_session(log)

    response = client.post(
        "/recruiter/sessions/sess-evidence/overrides",
        data={
            "dimension": "model_rationale",
            "original_value": "0.8",
            "override_value": "0.9",
            "reviewer_id": "recruiter-1",
            "reason": "Transcript shows stronger rationale than automated scorer captured.",
            "source_refs": "turn-1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = client.get("/recruiter/sessions/sess-evidence")
    assert "reviewer:recruiter-1" in page.text
    assert "0.90" in page.text
