"""ζ.3 — Candidate submits infinite-loop code; runtime kills it; session continues.

Flow:
  1. Create session (target_answers=1, max_probes=0).
  2. GET → challenger question.
  3. POST turn with code="while True: pass" (answer is empty string).
  4. GET → runner.advance():
       a. RequestExecution: runtime kills the loop (wall_time_exceeded or CPU limit).
       b. RuntimeFailed emitted → has_run=True for that turn.
       c. Loop continues: RequestScoring x 2 dims x 1 artifact -> RequestAggregate.
       d. Returns RunResult(ended) → redirect to result.
  5. GET /result → score present.

execution_timeout=2.0 ensures the subprocess is killed in ≤2 wall seconds.
The RUNTIME_MEMORY_BYTES default (512 MB) allows Python to start cleanly.
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
from core.events import RuntimeFailed, ScoreComputed
from core.rubric_loader import load_rubric

_MINI_RUBRIC = textwrap.dedent("""\
    name: test-runtime
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


def _make_client(tmp_path: Path) -> tuple[TestClient, SqliteEventLog]:
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
        execution_timeout=2.0,  # 2-second wall-time cap for test speed
    )
    app = make_app(log=log, runner=runner, target_answers=1, max_probes=0, output_dir=output_dir)
    client = TestClient(app, follow_redirects=True)
    return client, log


def test_infinite_loop_killed_session_completes(tmp_path: Path) -> None:
    """Infinite loop is killed; session reaches ScoreComputed."""
    client, log = _make_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "looper"})
    assert resp.status_code == 200
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # Submit only code, no prose answer.
    resp = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "", "code": "while True: pass", "turn_nonce": "inf1"},
    )
    assert resp.status_code == 200
    assert "/result" in str(resp.url)
    assert "Interview Complete" in resp.text

    # RuntimeFailed must be in the log
    events = log.get_session(session_id)
    runtime_failures = [e for e in events if isinstance(e.payload, RuntimeFailed)]
    assert len(runtime_failures) >= 1

    # ScoreComputed must be present despite the failure
    score_events = [e for e in events if isinstance(e.payload, ScoreComputed)]
    assert len(score_events) == 1


def test_infinite_loop_then_prose_answer_same_session(tmp_path: Path) -> None:
    """Two artifacts (code_cell + markdown) from same turn both get scored."""
    client, log = _make_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "mixed"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # Provide both prose and code in one turn
    resp = client.post(
        f"/sessions/{session_id}/turn",
        data={
            "answer": "I would use gradient boosting for this classification problem",
            "code": "while True: pass",
            "turn_nonce": "inf2",
        },
    )
    assert resp.status_code == 200
    assert "/result" in str(resp.url)

    events = log.get_session(session_id)
    # Runtime was attempted (and failed)
    assert any(isinstance(e.payload, RuntimeFailed) for e in events)
    # Session scored regardless
    assert any(isinstance(e.payload, ScoreComputed) for e in events)
