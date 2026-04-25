"""ζ.5 — Replay POST with same nonce produces exactly one TurnPosted.

Covers the e2e idempotency contract end-to-end: a browser retry or
network duplicate that re-POSTs the same form with the same turn_nonce
must not create duplicate candidate turns or duplicate artifacts.

Different from ε.3 (test_http_double_submit): this test exercises a
*complete* session flow and verifies idempotency at both the event-log
and the scoring layers.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from starlette.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from core.domain import Actor, Dimension
from core.events import ArtifactAttached, ScoreComputed, TurnPosted
from core.rubric_loader import load_rubric

_MINI_RUBRIC = textwrap.dedent("""\
    name: test-idem
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

_SCORED = (Dimension.model_rationale, Dimension.communication)


def _build_app(log: SqliteEventLog, output_dir: Path) -> object:
    router = ModelRouter(FakeRouter())
    rubric = load_rubric(yaml_str=_MINI_RUBRIC)
    aggregator = RubricAggregator(rubric, output_dir=output_dir)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.communication: LlmCommunicationScorer(router),
        },
        aggregator=aggregator,
        scored_dimensions=_SCORED,
    )
    return make_app(log=log, runner=runner, target_answers=1, max_probes=0, output_dir=output_dir)


def test_same_nonce_produces_one_candidate_turn(tmp_path: Path) -> None:
    """Posting the same nonce twice: exactly one candidate TurnPosted, one artifact."""
    output_dir = tmp_path / "outputs"
    log = SqliteEventLog(tmp_path / "log.db")
    app = _build_app(log, output_dir)

    # Follow redirects to advance FSM → challenger question in log
    redir = TestClient(app, follow_redirects=True)
    resp = redir.post("/sessions", data={"candidate_handle": "idem-alice"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # POST same nonce twice using no-redirect client (avoids FSM advance between posts)
    no_redir = TestClient(app, follow_redirects=False)
    nonce = "fixed-nonce-xyz"
    for _ in range(2):
        r = no_redir.post(
            f"/sessions/{session_id}/turn",
            data={"answer": "bias-variance trade-off", "code": "", "turn_nonce": nonce},
        )
        assert r.status_code == 303

    events = log.get_session(session_id)
    candidate_turns = [
        e for e in events
        if isinstance(e.payload, TurnPosted) and e.payload.actor == Actor.candidate
    ]
    assert len(candidate_turns) == 1, (
        f"expected 1 candidate TurnPosted, found {len(candidate_turns)}"
    )

    # Exactly one markdown artifact from that turn
    answer_artifacts = [
        e for e in events
        if isinstance(e.payload, ArtifactAttached)
        and e.payload.produced_by_turn_id == candidate_turns[0].payload.id
    ]
    assert len(answer_artifacts) == 1


def test_same_nonce_session_still_scores(tmp_path: Path) -> None:
    """After the duplicate POST is deduplicated, session advances to ScoreComputed."""
    output_dir = tmp_path / "outputs"
    log = SqliteEventLog(tmp_path / "log.db")
    app = _build_app(log, output_dir)

    redir = TestClient(app, follow_redirects=True)
    resp = redir.post("/sessions", data={"candidate_handle": "idem-bob"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # First POST (with redirect) → triggers scoring → result page
    nonce = "bob-nonce-1"
    resp2 = redir.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "regularisation prevents overfitting", "code": "", "turn_nonce": nonce},
    )
    assert resp2.status_code == 200
    assert "/result" in str(resp2.url)
    assert "Composite score" in resp2.text

    # Duplicate POST of same nonce (session already ended — idem_key stored)
    no_redir = TestClient(app, follow_redirects=False)
    resp3 = no_redir.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "regularisation prevents overfitting", "code": "", "turn_nonce": nonce},
    )
    assert resp3.status_code == 303  # still redirects; no new events

    events = log.get_session(session_id)
    score_events = [e for e in events if isinstance(e.payload, ScoreComputed)]
    assert len(score_events) == 1, "duplicate POST must not produce extra ScoreComputed"

    candidate_turns = [
        e for e in events
        if isinstance(e.payload, TurnPosted) and e.payload.actor == Actor.candidate
    ]
    assert len(candidate_turns) == 1


def test_different_nonces_each_produce_one_turn(tmp_path: Path) -> None:
    """Two distinct nonces → two candidate turns (sanity check for test validity)."""
    output_dir = tmp_path / "outputs"
    log = SqliteEventLog(tmp_path / "log.db")
    app = _build_app(log, output_dir)

    redir = TestClient(app, follow_redirects=True)
    no_redir = TestClient(app, follow_redirects=False)

    resp = redir.post("/sessions", data={"candidate_handle": "idem-carol"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # Two distinct nonces
    for nonce in ("nonce-A", "nonce-B"):
        r = no_redir.post(
            f"/sessions/{session_id}/turn",
            data={"answer": f"answer with {nonce}", "code": "", "turn_nonce": nonce},
        )
        assert r.status_code == 303

    events = log.get_session(session_id)
    candidate_turns = [
        e for e in events
        if isinstance(e.payload, TurnPosted) and e.payload.actor == Actor.candidate
    ]
    assert len(candidate_turns) == 2
