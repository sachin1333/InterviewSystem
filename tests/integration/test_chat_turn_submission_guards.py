from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from core.domain import Actor, Dimension, TurnKind
from core.events import ArtifactAttached, Envelope, SessionEnded, TurnPosted
from core.rubric_loader import load_rubric

_MINI_RUBRIC = textwrap.dedent("""\
    name: test-submit-guards
    version: 1
    dimensions:
      - name: model_rationale
        weight: 0.5
      - name: communication
        weight: 0.5
    aggregation:
      min_signals_per_dimension: 1
      confidence_weighting: true
""")


def _app(tmp_path: Path) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    aggregator = RubricAggregator(load_rubric(yaml_str=_MINI_RUBRIC), output_dir=tmp_path)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.communication: LlmCommunicationScorer(router),
        },
        aggregator=aggregator,
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )
    return TestClient(
        make_app(log=log, runner=runner, target_answers=1, max_probes=0, output_dir=tmp_path),
        follow_redirects=False,
    ), log


def _create_session(client: TestClient) -> str:
    response = client.post("/sessions", data={"candidate_handle": "alice"})
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def test_unknown_session_turn_post_is_rejected_without_events(tmp_path: Path) -> None:
    client, log = _app(tmp_path)

    response = client.post(
        "/sessions/not-a-session/turn",
        data={"answer": "injected", "code": "", "turn_nonce": "bad"},
    )

    assert response.status_code == 404
    assert log.get_session("not-a-session") == ()


def test_ended_session_turn_post_redirects_to_result_without_mutating_log(tmp_path: Path) -> None:
    client, log = _app(tmp_path)
    session_id = _create_session(client)
    client = TestClient(client.app, follow_redirects=True)
    client.get(f"/sessions/{session_id}")
    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "model rationale", "code": "", "turn_nonce": "n1"},
    )
    before = log.get_session(session_id)
    assert any(isinstance(env.payload, SessionEnded) for env in before)

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "late mutation", "code": "", "turn_nonce": "late"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/sessions/{session_id}/result"
    assert log.get_session(session_id) == before


def test_blank_turn_post_is_rejected_without_candidate_turn(tmp_path: Path) -> None:
    client, log = _app(tmp_path)
    session_id = _create_session(client)
    client.get(f"/sessions/{session_id}")

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "   ", "code": "   ", "turn_nonce": "blank"},
    )

    assert response.status_code == 400
    candidate_turns = [
        env
        for env in log.get_session(session_id)
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.candidate
    ]
    assert candidate_turns == []


def test_retry_after_partial_turn_write_attaches_artifact_to_original_turn(tmp_path: Path) -> None:
    client, log = _app(tmp_path)
    session_id = _create_session(client)
    client.get(f"/sessions/{session_id}")
    original_turn_id = "t-original"
    log.append(
        Envelope(
            session_id=session_id,
            seq=(log.last_seq(session_id) or 0) + 1,
            at=datetime.now(UTC),
            payload=TurnPosted(id=original_turn_id, actor=Actor.candidate, kind=TurnKind.answer),
            idem_key="recoverable",
        ),
        "recoverable",
    )

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "recovered answer", "code": "", "turn_nonce": "recoverable"},
    )

    assert response.status_code == 303
    artifacts = [
        env.payload
        for env in log.get_session(session_id)
        if isinstance(env.payload, ArtifactAttached) and env.payload.content == "recovered answer"
    ]
    assert len(artifacts) == 1
    assert artifacts[0].produced_by_turn_id == original_turn_id
