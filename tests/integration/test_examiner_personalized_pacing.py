from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import LlmExaminer
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.events import BackchannelPosted, TurnPosted
from core.rubric_loader import load_rubric

_RUBRIC = """
name: test
version: 1
dimensions:
  - name: communication
    weight: 1.0
aggregation:
  min_signals_per_dimension: 1
  confidence_weighting: true
"""


def test_examiner_probe_emits_backchannel_before_probe_turn(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    output_dir = tmp_path / "outputs"
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=LlmExaminer(router),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=output_dir),
        scored_dimensions=(),
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=output_dir, target_answers=1, max_probes=1))

    response = client.post(
        "/sessions",
        data={
            "candidate_handle": "x",
            "candidate_role": "ML Engineer",
            "declared_skills": "NLP",
            "resume_text": "Led ranking model at ShopCo.",
        },
        follow_redirects=True,
    )
    session_id = response.url.path.rsplit("/", 1)[-1]
    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would start with a retrieval baseline.", "turn_nonce": "n1"},
        follow_redirects=False,
    )
    client.get(f"/sessions/{session_id}")

    envelopes = log.get_session(session_id)
    backchannel_idx = next(i for i, env in enumerate(envelopes) if isinstance(env.payload, BackchannelPosted))
    probe_idx = next(
        i for i, env in enumerate(envelopes)
        if isinstance(env.payload, TurnPosted) and env.payload.actor.value == "examiner"
    )
    assert backchannel_idx < probe_idx
