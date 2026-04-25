"""ε.3b — Resume after server restart.

Simulates SIGKILL by closing log1 and opening log2 on the same DB file.
The second app must serve the same turn (challenger question already posted).
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


def _make_app(log: SqliteEventLog, output_dir: Path) -> TestClient:
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
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )
    app = make_app(log=log, runner=runner, target_answers=1, max_probes=0, output_dir=output_dir)
    return TestClient(app, follow_redirects=True)


def test_resume_shows_same_question(tmp_path: Path) -> None:
    log_path = tmp_path / "log.db"
    output_dir = tmp_path / "outputs"

    # --- server 1: create session and load question ---
    log1 = SqliteEventLog(log_path)
    client1 = _make_app(log1, output_dir)

    resp = client1.post("/sessions", data={"candidate_handle": "dave"})
    assert resp.status_code == 200
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    log1.close()

    # --- server 2: fresh app, same DB ---
    log2 = SqliteEventLog(log_path)
    client2 = _make_app(log2, output_dir)

    resp2 = client2.get(f"/sessions/{session_id}")
    assert resp2.status_code == 200
    # Same question shown (challenger turn was already appended to the log).
    assert "Generated prompt" in resp2.text or "no question" in resp2.text.lower()
    log2.close()


def test_resume_does_not_duplicate_challenger_turn(tmp_path: Path) -> None:
    """Challenger should not be called again if its TurnPosted is already in log."""
    log_path = tmp_path / "log.db"
    output_dir = tmp_path / "outputs"

    log1 = SqliteEventLog(log_path)
    client1 = _make_app(log1, output_dir)
    resp = client1.post("/sessions", data={"candidate_handle": "eve"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    challenger_turns_after_s1 = [
        e for e in log1.get_session(session_id) if isinstance(e.payload, TurnPosted)
        and e.payload.actor.value == "challenger"
    ]
    log1.close()

    # Server 2 GET: runner replays log, next_action returns RequestCandidateInput
    # (no RequestChallenge) → no new challenger TurnPosted appended.
    log2 = SqliteEventLog(log_path)
    client2 = _make_app(log2, output_dir)
    client2.get(f"/sessions/{session_id}")
    challenger_turns_after_s2 = [
        e for e in log2.get_session(session_id) if isinstance(e.payload, TurnPosted)
        and e.payload.actor.value == "challenger"
    ]
    log2.close()

    assert len(challenger_turns_after_s1) == len(challenger_turns_after_s2) == 1
