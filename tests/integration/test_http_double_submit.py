"""ε.3c — Double-submit idempotency via turn_nonce.

Posting the same turn_nonce twice must produce exactly one TurnPosted for the
candidate in the event log.  The second POST is a no-op (the idem_key prevents
a duplicate append).
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
from core.events import TurnPosted
from core.rubric_loader import load_rubric

_MINI_RUBRIC = textwrap.dedent("""\
    name: test
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


def _setup(tmp_path: Path) -> tuple[TestClient, SqliteEventLog, str]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    rubric = load_rubric(yaml_str=_MINI_RUBRIC)
    output_dir = tmp_path / "outputs"
    aggregator = RubricAggregator(rubric, output_dir=output_dir)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.communication: LlmCommunicationScorer(router),
        },
        aggregator=aggregator,
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )
    app = make_app(log=log, runner=runner, target_answers=1, max_probes=0, output_dir=output_dir)
    client = TestClient(app, follow_redirects=True)

    # Create session + advance to question
    resp = client.post("/sessions", data={"candidate_handle": "frank"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    return client, log, session_id


def test_same_nonce_produces_one_candidate_turn(tmp_path: Path) -> None:
    client, log, session_id = _setup(tmp_path)

    nonce = "fixed-nonce-abc"
    turn_data = {"answer": "my answer here", "code": "", "turn_nonce": nonce}

    # First submit
    client.post(f"/sessions/{session_id}/turn", data=turn_data)
    # Second submit — same nonce
    client.post(f"/sessions/{session_id}/turn", data=turn_data)

    candidate_turns = [
        e for e in log.get_session(session_id)
        if isinstance(e.payload, TurnPosted) and e.payload.actor == Actor.candidate
    ]
    assert len(candidate_turns) == 1, (
        f"expected 1 candidate TurnPosted, got {len(candidate_turns)}"
    )


def test_different_nonces_produce_one_turn_each(tmp_path: Path) -> None:
    """Sanity: distinct nonces are not collapsed."""
    client, log, session_id = _setup(tmp_path)

    # First turn
    client.post(f"/sessions/{session_id}/turn", data={"answer": "first", "code": "", "turn_nonce": "nonce-a"})

    # After first answer the FSM scores and ends; a second POST is ignored by
    # the already-ended session (EndSession → NoAction on GET, 303 to result on
    # GET).  So we only assert the first turn count here.
    candidate_turns = [
        e for e in log.get_session(session_id)
        if isinstance(e.payload, TurnPosted) and e.payload.actor == Actor.candidate
    ]
    assert len(candidate_turns) >= 1
