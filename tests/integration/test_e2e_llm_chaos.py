"""ζ.2 — All LLM calls time out; session still completes via fallbacks.

FakeRouter(fail_mode="timeout") forces every provider.call() to raise
TimeoutError.  ModelRouter top-tier catches it, tries mid-tier fallback
(also times out), then tries cheap-tier, and returns CHEAP_TIMEOUT_PLACEHOLDER.
Challenger parses the placeholder (has turn_kind + prompt_markdown keys → valid).
Scorers fail schema check → heuristic signals emitted.

Assertions:
  - Session ends with ScoreComputed despite 100% LLM failures.
  - Result page reachable.
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
from core.domain import Dimension
from core.events import ScoreComputed
from core.rubric_loader import load_rubric
from core.session_boot import replay

_MINI_RUBRIC = textwrap.dedent("""\
    name: test-chaos
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


def _make_chaos_client(tmp_path: Path) -> tuple[TestClient, SqliteEventLog]:
    """App where every LLM call times out immediately (no sleep between retries)."""
    log = SqliteEventLog(tmp_path / "log.db")
    # sleep=lambda _: None → retries are instant; no real waiting
    router = ModelRouter(FakeRouter(fail_mode="timeout"), sleep=lambda _: None)
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
    return client, log


def test_llm_chaos_session_completes(tmp_path: Path) -> None:
    """Session reaches ScoreComputed even when all LLM calls time out."""
    client, log = _make_chaos_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "chaos-alice"})
    assert resp.status_code == 200
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    resp = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "trade-off between bias and variance", "code": "", "turn_nonce": "c1"},
    )
    assert resp.status_code == 200
    assert "/result" in str(resp.url)
    assert "Interview Complete" in resp.text

    # verify ScoreComputed in log
    events = log.get_session(session_id)
    score_events = [e for e in events if isinstance(e.payload, ScoreComputed)]
    assert len(score_events) == 1, "exactly one ScoreComputed expected"


def test_llm_chaos_result_has_composite_score(tmp_path: Path) -> None:
    """Result page renders composite score even with heuristic-only signals."""
    client, _log = _make_chaos_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "chaos-bob"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "model selection rationale", "code": "", "turn_nonce": "c2"},
    )
    resp = client.get(f"/sessions/{session_id}/result")
    assert resp.status_code == 200
    assert "Composite score" in resp.text


def test_llm_chaos_no_orphan_sessions(tmp_path: Path) -> None:
    """Two chaos sessions in same DB — both complete; logs isolated."""
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter(fail_mode="timeout"), sleep=lambda _: None)
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

    session_ids = []
    for handle in ("carol", "dave"):
        resp = client.post("/sessions", data={"candidate_handle": handle})
        sid = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
        session_ids.append(sid)
        client.post(
            f"/sessions/{sid}/turn",
            data={"answer": f"answer from {handle}", "code": "", "turn_nonce": handle},
        )

    assert len(set(session_ids)) == 2
    for sid in session_ids:
        _, scores, _, _, _ = replay(sid, log)
        assert scores.get(sid) is not None, f"session {sid} should have a score"
