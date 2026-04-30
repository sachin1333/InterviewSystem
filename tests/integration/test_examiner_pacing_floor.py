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
from core.events import PacingFloorReached
from core.pacing import Pacer
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


def test_examiner_probe_emits_pacing_floor_event(tmp_path: Path) -> None:
    slept: list[float] = []
    log = SqliteEventLog(tmp_path / "log.db")
    runner = SessionRunner(
        challenger=LlmChallenger(ModelRouter(FakeRouter())),
        examiner=LlmExaminer(ModelRouter(FakeRouter())),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=tmp_path),
        scored_dimensions=(),
        pacer=Pacer(response_floor_ms=1, sleep=slept.append),
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=tmp_path, target_answers=1, max_probes=1), follow_redirects=True)
    resp = client.post("/sessions", data={"candidate_handle": "a"})
    session_id = resp.url.path.rsplit("/", 1)[-1]
    client.post(f"/sessions/{session_id}/turn", data={"answer": "baseline", "turn_nonce": "n"}, follow_redirects=False)
    client.get(f"/sessions/{session_id}")
    assert any(isinstance(env.payload, PacingFloorReached) for env in log.get_session(session_id))
