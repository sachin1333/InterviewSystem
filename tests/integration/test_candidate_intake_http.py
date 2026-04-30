from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.events import ProfileIngested
from core.rubric_loader import load_rubric

_MINI_RUBRIC = """
name: test
version: 1
dimensions:
  - name: communication
    weight: 1.0
aggregation:
  min_signals_per_dimension: 1
  confidence_weighting: true
"""


def _client(tmp_path: Path) -> tuple[TestClient, SqliteEventLog, Path]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    output_dir = tmp_path / "outputs"
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=RubricAggregator(load_rubric(yaml_str=_MINI_RUBRIC), output_dir=output_dir),
    )
    return TestClient(make_app(log=log, runner=runner, output_dir=output_dir)), log, output_dir


def test_create_session_ingests_candidate_details_and_materializes_user_md(tmp_path: Path) -> None:
    client, log, output_dir = _client(tmp_path)

    response = client.post(
        "/sessions",
        data={
            "candidate_handle": "Jane Candidate",
            "candidate_role": "ML Engineer",
            "years_experience": "4",
            "declared_skills": "Python, NLP",
            "resume_text": "jane@example.com\nLed transformer search relevance project.",
            "other_details": "Prefers careful offline evaluation before launch.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    session_id = response.headers["location"].split("/sessions/")[1]
    profile_events = [env.payload for env in log.get_session(session_id) if isinstance(env.payload, ProfileIngested)]
    assert len(profile_events) == 1
    assert profile_events[0].declared_skills == ("Python", "NLP")
    user_md = output_dir / "sessions" / session_id / "USER.md"
    assert user_md.exists()
    rendered = user_md.read_text(encoding="utf8")
    assert "Led transformer search relevance project" in rendered
    assert "jane@example.com" not in rendered
