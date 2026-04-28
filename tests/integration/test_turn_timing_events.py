from __future__ import annotations

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
from core.domain import Actor, Problem, ProblemId
from core.events import CandidateJoined, Envelope, SessionStarted, TurnPosted, TurnTimingObserved


def _runner(tmp_path: Path) -> SessionRunner:
    router = ModelRouter(FakeRouter())
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    return SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=aggregator,
        problems=[Problem(id=ProblemId("p1"), opener_text="Explain a model trade-off.")],
    )


def test_problem_opener_emits_server_timing_event(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "events.db")
    app = make_app(log=log, runner=_runner(tmp_path), output_dir=tmp_path)
    client = TestClient(app, follow_redirects=True)

    response = client.post("/sessions", data={"candidate_handle": "alice"})

    assert response.status_code == 200
    timings = [env.payload for env in log.all() if isinstance(env.payload, TurnTimingObserved)]
    challenger_turn_ids = {
        env.payload.id
        for env in log.all()
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.challenger
    }
    assert len(timings) == 1
    assert timings[0].turn_id in challenger_turn_ids
    assert timings[0].phase == "challenger_opener"
    assert timings[0].context_assembled_ms >= timings[0].submit_received_ms
    assert timings[0].first_token_ms >= timings[0].context_assembled_ms
    assert timings[0].first_paint_ms >= timings[0].first_token_ms


def test_candidate_submit_emits_server_timing_event(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "events.db")
    session_id = "s-latency"
    now = datetime.now(UTC)
    log.append(Envelope(
        session_id=session_id,
        seq=1,
        at=now,
        payload=SessionStarted(rubric_version="ds", target_answers=1, max_probes_per_prompt=0),
    ))
    log.append(Envelope(
        session_id=session_id,
        seq=2,
        at=now,
        payload=CandidateJoined(candidate_handle="alice"),
    ))
    app = make_app(log=log, runner=_runner(tmp_path), output_dir=tmp_path)
    client = TestClient(app)
    client.get(f"/sessions/{session_id}")

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "AUC can hide segment failures.", "turn_nonce": "n1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    timings = [env.payload for env in log.get_session(session_id) if isinstance(env.payload, TurnTimingObserved)]
    candidate_turn_ids = {
        env.payload.id
        for env in log.get_session(session_id)
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.candidate
    }
    candidate_timings = [timing for timing in timings if timing.turn_id in candidate_turn_ids]
    assert len(candidate_timings) == 1
    timing = candidate_timings[0]
    assert timing.phase == "candidate_submit"
    assert timing.submit_received_ms == 0
    assert timing.context_assembled_ms >= timing.submit_received_ms
    assert timing.first_token_ms >= timing.context_assembled_ms
    assert timing.first_paint_ms >= timing.first_token_ms
