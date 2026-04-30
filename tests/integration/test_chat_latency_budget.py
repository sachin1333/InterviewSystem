from __future__ import annotations

import textwrap
import time
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
from core.domain import Dimension
from core.rubric_loader import load_rubric

_RUBRIC = textwrap.dedent("""\
name: latency-smoke
version: 1
dimensions:
  - name: communication
    weight: 1.0
aggregation:
  min_signals_per_dimension: 1
  confidence_weighting: true
""")


def test_chat_probe_paint_stays_under_three_seconds_with_slow_model_fallback(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "latency.db")
    # FakeRouter's default response is valid challenger JSON but missing the examiner
    # `action` key. At 750ms/provider call this reproduces malformed-model fallback
    # cost while keeping the test deterministic.
    router = ModelRouter(FakeRouter(latency=0.75), max_retries=1, sleep=lambda _: None)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=LlmExaminer(router),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=tmp_path / "outputs"),
        scored_dimensions=(Dimension.communication,),
    )
    client = TestClient(
        make_app(log=log, runner=runner, output_dir=tmp_path / "outputs", target_answers=1, max_probes=1),
        follow_redirects=True,
    )
    response = client.post("/sessions", data={"candidate_handle": "latency-candidate"})
    assert response.status_code == 200
    session_id = str(response.url).split("/sessions/")[1].split("/")[0].split("?")[0]
    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would start with a retrieval baseline.", "turn_nonce": "n1"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    started = time.perf_counter()
    response = client.get(f"/sessions/{session_id}")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 3.0, f"examiner probe paint took {elapsed:.3f}s"
