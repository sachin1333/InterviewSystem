from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import create_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension
from core.events import CandidateJoined, Envelope, ProblemIntroduced, SessionStarted
from core.problem_bank import ProblemBank
from core.session_boot import replay


def _aggregator(tmp_path: Path) -> RubricAggregator:
    return RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )


def _append_started(log: SqliteEventLog, session_id: str) -> None:
    now = datetime.now(UTC)
    log.append(Envelope(
        session_id=session_id,
        seq=1,
        at=now,
        payload=SessionStarted(
            rubric_version="ds-ml-engineer@1",
            target_answers=1,
            max_probes_per_prompt=0,
            pack_id="ds-ml-v1",
        ),
    ))
    log.append(Envelope(
        session_id=session_id,
        seq=2,
        at=now,
        payload=CandidateJoined(candidate_handle="alice"),
    ))


def test_session_runner_draws_problem_sequence_from_bank(tmp_path: Path) -> None:
    bank = ProblemBank.from_yaml(Path("templates/problem_banks/ds-ml-engineer-v1.yaml"))
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=_aggregator(tmp_path),
        scored_dimensions=(
            Dimension.problem_framing,
            Dimension.model_rationale,
            Dimension.experiment_design,
            Dimension.insight_interp,
            Dimension.communication,
        ),
        problem_bank=bank,
    )
    session_id = "sess-bank-001"
    _append_started(log, session_id)

    result = runner.advance(session_id, log)

    expected_first = bank.pick_sequence(session_id)[0]
    assert result.state == "await_input"
    assert result.display_text == expected_first.opener_text
    events = [env.payload for env in log.get_session(session_id)]
    introduced = [event for event in events if isinstance(event, ProblemIntroduced)]
    assert introduced[0].problem_id == expected_first.id
    assert introduced[0].ordinal == 1
    sessions, _, _, _, _ = replay(session_id, log)
    record = sessions.get(session_id)
    assert record is not None
    assert record["current_problem_id"] == expected_first.id


def test_create_app_loads_problem_bank_for_new_sessions(tmp_path: Path) -> None:
    app = create_app(db_path=str(tmp_path / "app.db"))

    runner: SessionRunner = app.state.runner

    assert runner.problem_bank is not None
    assert len(runner.problem_bank) >= 4
    assert runner.problem_bank.prewarm_openers()[0]
