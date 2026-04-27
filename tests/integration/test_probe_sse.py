from pathlib import Path

from fastapi.testclient import TestClient

from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.scorer.aggregator import RubricAggregator
from adapters.challenger.llm_challenger import LlmChallenger
from adapters.examiner.llm_examiner import LlmExaminer
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from core.case_bank import CaseBank


def test_probe_sse_streams_tokens(tmp_path: Path) -> None:
    bank = CaseBank.from_yaml(Path("templates/cases/cold_start_bank.yaml"))
    fake = FakeRouter(scripted_streams=[["What ", "would ", "you ", "do ", "next?"]])
    router = ModelRouter(fake)
    log = SqliteEventLog(str(tmp_path / "log.db"))
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    runner = SessionRunner(
        challenger=LlmChallenger(router, case_bank=bank),
        examiner=LlmExaminer(router),
        aggregator=aggregator,
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, max_probes=1)
    client = TestClient(app)

    r = client.post("/sessions", data={"candidate_handle": "alice"}, follow_redirects=True)
    session_id = r.url.path.rsplit("/", 1)[-1]
    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I'd start by framing the metric.", "turn_nonce": "n1"},
    )

    with client.stream("GET", f"/sessions/{session_id}/probe-stream") as stream:
        chunks = [line for line in stream.iter_lines() if line.startswith("data: ")]
    body = "".join(c.removeprefix("data: ") for c in chunks)
    assert "What" in body and "next" in body
