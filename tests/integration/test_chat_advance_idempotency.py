from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Actor, Problem, ProblemId
from core.events import ProblemIntroduced, TurnPosted


def _runner(tmp_path: Path, *, problems: list[Problem] | None = None) -> SessionRunner:
    router = ModelRouter(FakeRouter())
    return SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=RubricAggregator.from_yaml(
            Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
            output_dir=tmp_path,
        ),
        problems=problems or [],
    )


def _new_session_no_follow(client: TestClient) -> str:
    response = client.post("/sessions", data={"candidate_handle": "race"})
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def _concurrent_gets(app: Any, session_id: str) -> None:
    def get_once() -> int:
        client = TestClient(app, raise_server_exceptions=True)
        return client.get(f"/sessions/{session_id}").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(get_once), pool.submit(get_once)]
        statuses = [future.result() for future in as_completed(futures)]
    assert sorted(statuses) == [200, 200]


def test_concurrent_legacy_get_introduces_one_challenger_turn(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    app = make_app(log=log, runner=_runner(tmp_path), output_dir=tmp_path)
    session_id = _new_session_no_follow(TestClient(app, follow_redirects=False))

    _concurrent_gets(app, session_id)

    challenger_turns = [
        env.payload
        for env in log.get_session(session_id)
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.challenger
    ]
    assert len(challenger_turns) == 1


def test_concurrent_problem_get_introduces_one_problem_and_opener(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    app = make_app(
        log=log,
        runner=_runner(tmp_path, problems=[Problem(id=ProblemId("p1"), opener_text="P1")]),
        output_dir=tmp_path,
    )
    session_id = _new_session_no_follow(TestClient(app, follow_redirects=False))

    _concurrent_gets(app, session_id)

    introduced = [env for env in log.get_session(session_id) if isinstance(env.payload, ProblemIntroduced)]
    challenger_turns = [
        env.payload
        for env in log.get_session(session_id)
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.challenger
    ]
    assert len(introduced) == 1
    assert len(challenger_turns) == 1
