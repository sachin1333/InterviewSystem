"""Integration test: 5-stage Socratic text-mode chat flow."""
from pathlib import Path

from core.case_bank import CaseBank
from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import LlmExaminer
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.case_loader import load_case


def _make_client(tmp_path: Path) -> tuple[TestClient, str]:
    bank = CaseBank.from_yaml(Path("templates/cases/cold_start_bank.yaml"))
    case = load_case(Path("templates/cases/multi_stage_case_v1.yaml"))
    fake = FakeRouter()
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
        case=case,
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, max_probes=0)
    client = TestClient(app)
    r = client.post("/sessions", data={"candidate_handle": "x"}, follow_redirects=True)
    session_id = r.url.path.rsplit("/", 1)[-1]
    return client, session_id


def test_five_stage_text_socratic_flow(tmp_path: Path) -> None:
    """Each stage delivers one distinct interviewer turn; session ends after 5 answers."""
    client, session_id = _make_client(tmp_path)

    interviewer_turns_seen = 0
    for stage_idx in range(5):
        # GET should show exactly one more interviewer message than before.
        page = client.get(f"/sessions/{session_id}").text
        new_count = page.count('class="msg interviewer"')
        assert new_count == interviewer_turns_seen + 1, (
            f"stage {stage_idx}: expected {interviewer_turns_seen + 1} interviewer turns, got {new_count}"
        )
        interviewer_turns_seen = new_count

        # Submit candidate answer.
        client.post(
            f"/sessions/{session_id}/turn",
            data={"answer": f"answer for stage {stage_idx}", "turn_nonce": f"n{stage_idx}"},
        )

    # After 5 answers, session should redirect to /result.
    r = client.get(f"/sessions/{session_id}", follow_redirects=False)
    assert r.status_code in (303, 307)
    assert "/result" in r.headers["location"]
