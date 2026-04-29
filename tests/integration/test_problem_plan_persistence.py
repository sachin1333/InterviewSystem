from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Problem, ProblemId
from core.events import ProblemIntroduced, ProblemPlanSelected
from core.problem_bank import ProblemBank, ProblemBankEntry


def _bank(version: str, problems: list[Problem]) -> ProblemBank:
    return ProblemBank([ProblemBankEntry(problem=p, tags=()) for p in problems], version=version)


def _runner(tmp_path: Path, bank: ProblemBank) -> SessionRunner:
    router = ModelRouter(FakeRouter())
    return SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=RubricAggregator.from_yaml(
            Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path
        ),
        problem_bank=bank,
    )


def test_problem_plan_is_selected_once_and_survives_bank_reorder(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    p1 = Problem(id=ProblemId("p1"), opener_text="First")
    p2 = Problem(id=ProblemId("p2"), opener_text="Second")
    original_bank = _bank("v1", [p1, p2])
    app = make_app(log=log, runner=_runner(tmp_path, original_bank), output_dir=tmp_path)
    client = TestClient(app, follow_redirects=True)
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]

    runner: SessionRunner = app.state.runner
    runner.problem_bank = _bank("v2", [p2, p1])
    client.get(f"/sessions/{session_id}")

    events = [env.payload for env in log.get_session(session_id)]
    plans = [event for event in events if isinstance(event, ProblemPlanSelected)]
    introduced = [event for event in events if isinstance(event, ProblemIntroduced)]
    assert len(plans) == 1
    assert tuple(plans[0].problem_ids) == tuple(
        problem.id for problem in original_bank.pick_sequence(session_id)
    )
    assert introduced[0].problem_id == plans[0].problem_ids[0]
