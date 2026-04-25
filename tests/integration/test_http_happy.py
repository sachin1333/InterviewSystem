"""ε.3a — Happy-path full session over HTTP.

Single-answer session (target_answers=1, max_probes=0) using FakeRouter:
  1. POST /sessions → creates session, redirects
  2. GET /sessions/{id} → runner advances (challenge), renders question
  3. POST /sessions/{id}/turn → submits answer, redirects
  4. GET /sessions/{id} → runner advances (score + aggregate), redirects to result
  5. GET /sessions/{id}/result → 200, shows score

Assertions:
  - Score page reachable and contains composite score text
  - Feedback markdown file written to tmp output_dir
  - All redirects resolve cleanly
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


def _make_client(tmp_path: Path) -> tuple[TestClient, str]:
    """Return (client, session_id) after completing POST /sessions."""
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
    app = make_app(
        log=log, runner=runner,
        target_answers=1, max_probes=0,
        output_dir=output_dir,
    )
    client = TestClient(app, follow_redirects=True)
    return client, log


def test_full_session_reaches_result_page(tmp_path: Path) -> None:
    client, _log = _make_client(tmp_path)

    # 1. Create session
    resp = client.post("/sessions", data={"candidate_handle": "alice"})
    assert resp.status_code == 200, resp.text

    # After redirect chain we land on the turn page (question displayed)
    assert "Generated prompt" in resp.text or "no question" in resp.text.lower()
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # 2. Submit answer (triggers score+aggregate on next GET)
    resp = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would use logistic regression because it is interpretable", "code": "", "turn_nonce": "nonce1"},
    )
    assert resp.status_code == 200, resp.text
    # After redirect chain: should land on result page
    assert "/result" in str(resp.url)
    assert "Interview Complete" in resp.text


def test_result_page_has_score(tmp_path: Path) -> None:
    client, _log = _make_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "bob"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "trade-off between bias and variance baseline model", "code": "", "turn_nonce": "n2"},
    )
    resp = client.get(f"/sessions/{session_id}/result")
    assert resp.status_code == 200
    assert "Composite score" in resp.text


def test_feedback_file_written(tmp_path: Path) -> None:
    client, _log = _make_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "carol"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "feature importance from random forest", "code": "", "turn_nonce": "n3"},
    )

    output_dir = tmp_path / "outputs"
    feedback_files = list(output_dir.glob(f"{session_id}_feedback.md"))
    assert len(feedback_files) == 1
    content = feedback_files[0].read_text()
    assert "Candidate Feedback" in content
