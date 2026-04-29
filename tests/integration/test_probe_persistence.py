from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import ExaminerOutcome
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Actor, Problem, ProblemId, TurnKind
from core.events import ArtifactAttached, ExaminerFailed, TurnPosted

_FALLBACK_PROBE = "Please clarify your assumptions"


class FailingExaminer:
    def review(
        self, session_id: str, recent_turns: list[Any], **kwargs: Any
    ) -> tuple[ExaminerOutcome, ExaminerFailed]:
        del session_id, recent_turns, kwargs
        return ExaminerOutcome(ok_to_advance=True), ExaminerFailed(reason="provider down")

    def iter_review(self, *args: Any, **kwargs: Any) -> Iterator[str]:
        del args, kwargs
        raise AssertionError("probe-stream must not call the model")


def _app(tmp_path: Path, examiner: object) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=examiner,  # type: ignore[arg-type]
        aggregator=RubricAggregator.from_yaml(
            Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path
        ),
        problems=[Problem(id=ProblemId("p1"), opener_text="P1")],
    )
    return TestClient(make_app(log=log, runner=runner, output_dir=tmp_path), follow_redirects=True), log


def test_examiner_failure_posts_visible_fallback_probe_artifact(tmp_path: Path) -> None:
    client, log = _app(tmp_path, FailingExaminer())
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would define the metric first.", "turn_nonce": "n1"},
    )

    assert response.status_code == 200
    assert _FALLBACK_PROBE in response.text
    events = [env.payload for env in log.get_session(session_id)]
    probe_turns = [
        event
        for event in events
        if isinstance(event, TurnPosted)
        and event.actor == Actor.examiner
        and event.kind == TurnKind.probe
    ]
    probe_artifacts = [
        event
        for event in events
        if isinstance(event, ArtifactAttached) and event.produced_by_turn_id == probe_turns[-1].id
    ]
    assert len(probe_artifacts) == 1
    assert _FALLBACK_PROBE in (probe_artifacts[0].content or "")


def test_probe_stream_streams_persisted_text_without_model_call(tmp_path: Path) -> None:
    client, _log = _app(tmp_path, FailingExaminer())
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]
    client.post(f"/sessions/{session_id}/turn", data={"answer": "Answer", "turn_nonce": "n1"})

    response = client.get(f"/sessions/{session_id}/probe-stream")

    assert response.status_code == 200
    assert _FALLBACK_PROBE in response.text
