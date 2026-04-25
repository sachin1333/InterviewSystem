"""ζ.1 — Happy-path 3-turn session with all 4 dimensions scored.

target_answers=3, max_probes=0, four scorers wired (problem_framing,
model_rationale, insight_interp, communication).  FakeRouter returns
non-scorer JSON → all scorers fall through to heuristic signals (conf=0.2).

Assertions:
  - Result page reachable after 3 answers.
  - Composite score rendered.
  - All 4 dimensions present in per_dimension (checked via ScoreStore).
  - Feedback markdown file written.
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
from adapters.scorer.llm_insight_interp_scorer import LlmInsightInterpScorer
from adapters.scorer.llm_problem_framing_scorer import LlmProblemFramingScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from core.domain import Dimension
from core.rubric_loader import load_rubric
from core.session_boot import replay

_FOUR_DIM_RUBRIC = textwrap.dedent("""\
    name: test-4dim
    version: 1
    dimensions:
      - name: problem_framing
        weight: 0.25
      - name: model_rationale
        weight: 0.25
      - name: insight_interp
        weight: 0.25
      - name: communication
        weight: 0.25
    aggregation:
      min_signals_per_dimension: 1
      confidence_weighting: true
""")

_SCORED_4 = (
    Dimension.problem_framing,
    Dimension.model_rationale,
    Dimension.insight_interp,
    Dimension.communication,
)


def _make_client(tmp_path: Path) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    rubric = load_rubric(yaml_str=_FOUR_DIM_RUBRIC)
    output_dir = tmp_path / "outputs"
    aggregator = RubricAggregator(rubric, output_dir=output_dir)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        scorers={
            Dimension.problem_framing: LlmProblemFramingScorer(router),
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.insight_interp: LlmInsightInterpScorer(router),
            Dimension.communication: LlmCommunicationScorer(router),
        },
        aggregator=aggregator,
        scored_dimensions=_SCORED_4,
    )
    app = make_app(log=log, runner=runner, target_answers=3, max_probes=0, output_dir=output_dir)
    client = TestClient(app, follow_redirects=True)
    return client, log


def test_3turn_session_reaches_result_page(tmp_path: Path) -> None:
    client, _log = _make_client(tmp_path)

    # create session; redirect chain lands on question page
    resp = client.post("/sessions", data={"candidate_handle": "alpha"})
    assert resp.status_code == 200
    assert "Generated prompt" in resp.text or "Fallback prompt" in resp.text or "no question" in resp.text.lower()
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    # answer 1 → question 2
    resp = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "Frame the problem as a churn prediction task with F1 metric", "code": "", "turn_nonce": "n1"},
    )
    assert resp.status_code == 200
    assert "/result" not in str(resp.url)  # not done yet

    # answer 2 → question 3
    resp = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would use logistic regression as baseline model for interpretability", "code": "", "turn_nonce": "n2"},
    )
    assert resp.status_code == 200
    assert "/result" not in str(resp.url)

    # answer 3 → scoring → result
    resp = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "The model result suggests feature X has high importance for prediction", "code": "", "turn_nonce": "n3"},
    )
    assert resp.status_code == 200
    assert "/result" in str(resp.url)
    assert "Interview Complete" in resp.text
    assert "Composite score" in resp.text


def test_all_4_dimensions_scored(tmp_path: Path) -> None:
    client, log = _make_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "beta"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    for i, answer in enumerate(
        [
            "Define the business goal and constrain the scope",
            "Select a model based on interpretability trade-off",
            "Interpret the result and conclude insight from the trend",
        ],
        start=1,
    ):
        resp = client.post(
            f"/sessions/{session_id}/turn",
            data={"answer": answer, "code": "", "turn_nonce": f"nonce{i}"},
        )

    # verify all 4 dims in ScoreStore
    _, scores, _, _, _ = replay(session_id, log)
    score = scores.get(session_id)
    assert score is not None, "session must have a ScoreComputed event"
    assert set(score.per_dimension.keys()) == set(_SCORED_4), (
        f"expected {set(d.value for d in _SCORED_4)}, got {set(score.per_dimension.keys())}"
    )


def test_feedback_file_written_4dim(tmp_path: Path) -> None:
    client, _log = _make_client(tmp_path)

    resp = client.post("/sessions", data={"candidate_handle": "gamma"})
    session_id = str(resp.url).split("/sessions/")[1].split("/")[0].split("?")[0]

    for i in range(3):
        client.post(
            f"/sessions/{session_id}/turn",
            data={"answer": f"answer {i}", "code": "", "turn_nonce": f"fbn{i}"},
        )

    output_dir = tmp_path / "outputs"
    files = list(output_dir.glob(f"{session_id}_feedback.md"))
    assert len(files) == 1
    assert "Candidate Feedback" in files[0].read_text()
