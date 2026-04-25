"""ζ.6 — Five concurrent sessions; event logs are isolated.

Five threads each complete an independent session against the same shared
SQLite event log.  After all threads finish:
  - Every session has exactly one ScoreComputed event.
  - No session's events appear in another session's projection.
  - All session IDs are distinct.

SQLite WAL mode + check_same_thread=False ensures concurrent writes
serialise at the DB layer without deadlock.
"""
from __future__ import annotations

import textwrap
import threading
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
    name: test-concurrent
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

_N_SESSIONS = 5


def _make_shared_app(log: SqliteEventLog, output_dir: Path) -> object:
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
    return make_app(log=log, runner=runner, target_answers=1, max_probes=0, output_dir=output_dir)


def test_concurrent_sessions_isolated(tmp_path: Path) -> None:
    """Five threads each complete a session; all reach ScoreComputed; IDs distinct."""
    output_dir = tmp_path / "outputs"
    log = SqliteEventLog(tmp_path / "log.db")
    app = _make_shared_app(log, output_dir)

    session_ids: dict[int, str] = {}
    errors: list[Exception] = []
    lock = threading.Lock()

    def run_session(idx: int) -> None:
        try:
            client = TestClient(app, follow_redirects=True)
            resp = client.post("/sessions", data={"candidate_handle": f"concurrent-{idx}"})
            assert resp.status_code == 200
            sid = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

            resp2 = client.post(
                f"/sessions/{sid}/turn",
                data={
                    "answer": f"answer from thread {idx}: model selection rationale",
                    "code": "",
                    "turn_nonce": f"conc-nonce-{idx}",
                },
            )
            assert resp2.status_code == 200
            assert "/result" in str(resp2.url), f"thread {idx} did not reach result page"

            with lock:
                session_ids[idx] = sid
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=run_session, args=(i,)) for i in range(_N_SESSIONS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"concurrent session errors: {errors}"
    assert len(session_ids) == _N_SESSIONS

    # All session IDs distinct
    ids = list(session_ids.values())
    assert len(set(ids)) == _N_SESSIONS, f"duplicate session IDs: {ids}"

    # Every session has exactly one ScoreComputed; projections are isolated
    for idx, sid in session_ids.items():
        events = log.get_session(sid)
        score_events = [e for e in events if isinstance(e.payload, ScoreComputed)]
        assert len(score_events) == 1, (
            f"session {sid} (thread {idx}) expected 1 ScoreComputed, got {len(score_events)}"
        )

        _, scores, _, _, _ = replay(sid, log)
        assert scores.get(sid) is not None, f"ScoreStore empty for session {sid}"

        # No events from another session pollute this session's log
        for e in events:
            assert e.session_id == sid, (
                f"foreign session_id {e.session_id!r} found in session {sid!r}"
            )


def test_concurrent_sessions_result_pages_correct(tmp_path: Path) -> None:
    """Each session's result page shows its own score (no cross-contamination)."""
    output_dir = tmp_path / "outputs"
    log = SqliteEventLog(tmp_path / "log.db")
    app = _make_shared_app(log, output_dir)

    session_ids: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def run_session(idx: int) -> None:
        try:
            client = TestClient(app, follow_redirects=True)
            resp = client.post("/sessions", data={"candidate_handle": f"verify-{idx}"})
            sid = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
            client.post(
                f"/sessions/{sid}/turn",
                data={"answer": f"verify answer {idx}", "code": "", "turn_nonce": f"vn-{idx}"},
            )
            with lock:
                session_ids.append(sid)
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=run_session, args=(i,)) for i in range(_N_SESSIONS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors
    assert len(session_ids) == _N_SESSIONS

    # Each result page shows session's own score
    client = TestClient(app, follow_redirects=True)
    for sid in session_ids:
        resp = client.get(f"/sessions/{sid}/result")
        assert resp.status_code == 200
        assert "Composite score" in resp.text
        assert sid in resp.text
