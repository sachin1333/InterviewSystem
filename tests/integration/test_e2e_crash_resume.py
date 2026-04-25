"""ζ.4 — SIGKILL simulation: session survives mid-flow crash.

Simulates a hard server restart by:
  1. Server 1 creates the session (POST /sessions, no redirect follow) and
     then closes its log connection — emulating a kill before any FSM step.
  2. Server 2 opens the same SQLite file, advances through the full session.

Also tests a deeper crash point: after turn 1 is posted but before
scoring, a second crash is simulated.  Server 3 must replay and finish.
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

_MINI_RUBRIC = textwrap.dedent("""\
    name: test-crash
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


def _open_app(log_path: Path, output_dir: Path) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(log_path)
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
    return TestClient(app, follow_redirects=True), log


def test_crash_before_fsm_step(tmp_path: Path) -> None:
    """Crash immediately after session events written; server 2 drives full interview."""
    log_path = tmp_path / "log.db"
    output_dir = tmp_path / "outputs"

    # Server 1: write SessionStarted + CandidateJoined only (no redirect follow)
    log1 = SqliteEventLog(log_path)
    client1_no_redir = TestClient(
        make_app(
            log=log1,
            runner=SessionRunner(
                challenger=LlmChallenger(ModelRouter(FakeRouter())),
                scorers={
                    Dimension.model_rationale: LlmRationaleScorer(ModelRouter(FakeRouter())),
                    Dimension.communication: LlmCommunicationScorer(ModelRouter(FakeRouter())),
                },
                aggregator=RubricAggregator(
                    load_rubric(yaml_str=_MINI_RUBRIC), output_dir=output_dir
                ),
                scored_dimensions=(Dimension.model_rationale, Dimension.communication),
            ),
            target_answers=1,
            max_probes=0,
            output_dir=output_dir,
        ),
        follow_redirects=False,
    )
    resp1 = client1_no_redir.post("/sessions", data={"candidate_handle": "crash-eve"})
    assert resp1.status_code == 303
    location = resp1.headers["location"]
    session_id = location.split("/sessions/")[1].split("/")[0].split("?")[0]
    log1.close()  # "SIGKILL"

    # Server 2: same DB, complete full interview
    client2, log2 = _open_app(log_path, output_dir)
    resp2 = client2.get(f"/sessions/{session_id}")
    assert resp2.status_code == 200
    assert "Generated prompt" in resp2.text or "Fallback prompt" in resp2.text

    resp2 = client2.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "feature importance and model selection", "code": "", "turn_nonce": "cr1"},
    )
    assert resp2.status_code == 200
    assert "/result" in str(resp2.url)
    assert "Interview Complete" in resp2.text
    log2.close()


def test_crash_after_turn1_before_scoring(tmp_path: Path) -> None:
    """Crash after answer posted but before scoring; server 2 scores and finishes."""
    log_path = tmp_path / "log.db"
    output_dir = tmp_path / "outputs"

    # Server 1: create session + answer turn 1 (follow_redirects=False on turn POST)
    client1, log1 = _open_app(log_path, output_dir)

    # Create session (follows redirect → challenger question)
    resp1 = client1.post("/sessions", data={"candidate_handle": "crash-frank"})
    session_id = str(resp1.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # Post answer but do NOT follow redirect to avoid scoring
    no_redir_client = TestClient(
        make_app(
            log=log1,
            runner=SessionRunner(
                challenger=LlmChallenger(ModelRouter(FakeRouter())),
                scorers={
                    Dimension.model_rationale: LlmRationaleScorer(ModelRouter(FakeRouter())),
                    Dimension.communication: LlmCommunicationScorer(ModelRouter(FakeRouter())),
                },
                aggregator=RubricAggregator(
                    load_rubric(yaml_str=_MINI_RUBRIC), output_dir=output_dir
                ),
                scored_dimensions=(Dimension.model_rationale, Dimension.communication),
            ),
            target_answers=1,
            max_probes=0,
            output_dir=output_dir,
        ),
        follow_redirects=False,
    )
    resp_turn = no_redir_client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "logistic regression with L2 regularisation", "code": "", "turn_nonce": "cr2"},
    )
    assert resp_turn.status_code == 303
    log1.close()  # "SIGKILL" after answer but before scoring

    # Server 2: replays log → picks up scoring where server 1 left off
    client2, log2 = _open_app(log_path, output_dir)
    resp2 = client2.get(f"/sessions/{session_id}")
    # Should redirect to result (scoring + aggregation happen inside advance())
    assert "/result" in str(resp2.url)
    assert "Composite score" in resp2.text

    events = log2.get_session(session_id)
    assert any(isinstance(e.payload, ScoreComputed) for e in events)
    log2.close()
