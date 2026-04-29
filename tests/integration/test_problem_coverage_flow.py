from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import CoverageContext, ExaminerOutcome
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, Problem, ProblemId
from core.events import ProblemCoverageObserved


class CapturingExaminer:
    def __init__(self) -> None:
        self.coverages: list[CoverageContext] = []

    def review(self, session_id: str, recent_turns: list[Any], **kwargs: Any) -> tuple[ExaminerOutcome, None]:
        del session_id, recent_turns
        self.coverages.append(kwargs["coverage"])
        return ExaminerOutcome(
            action="probe",
            probe_text="What metric would you inspect first?",
            ok_to_advance=False,
        ), None


def test_latest_candidate_answer_emits_problem_coverage_before_examiner_review(
    tmp_path: Path,
) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    examiner = CapturingExaminer()
    problem = Problem(
        id=ProblemId("p1"),
        opener_text="Frame churn.",
        context="Look for metric definition and cohort/data quality risks.",
        target_dimensions=(Dimension.problem_framing, Dimension.communication),
        dim_thresholds={Dimension.problem_framing: 0.5, Dimension.communication: 0.5},
    )
    app = make_app(
        log=log,
        runner=SessionRunner(
            challenger=LlmChallenger(router),
            examiner=examiner,  # type: ignore[arg-type]
            aggregator=RubricAggregator.from_yaml(
                Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path
            ),
            problems=[problem],
        ),
        output_dir=tmp_path,
    )
    client = TestClient(app, follow_redirects=True)
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]

    client.post(
        f"/sessions/{session_id}/turn",
        data={
            "answer": "I would define the churn metric, cohort, and stakeholder decision.",
            "turn_nonce": "n1",
        },
    )

    coverage_events = [
        env.payload for env in log.get_session(session_id) if isinstance(env.payload, ProblemCoverageObserved)
    ]
    assert {event.dimension for event in coverage_events} == {
        Dimension.problem_framing,
        Dimension.communication,
    }
    assert examiner.coverages
    assert examiner.coverages[0].signal_map[Dimension.problem_framing.value] > 0
    assert examiner.coverages[0].problem_context == problem.context
