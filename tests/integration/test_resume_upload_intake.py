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


def test_txt_resume_upload_materializes_scrubbed_user_md_and_raw_audit_file(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    output_dir = tmp_path / "outputs"
    runner = SessionRunner(
        challenger=LlmChallenger(ModelRouter(FakeRouter())),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=output_dir),
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=output_dir))
    response = client.post(
        "/sessions",
        data={"candidate_handle": "Jane Candidate", "declared_skills": "Python"},
        files={"resume_file": ("resume.txt", b"jane@example.com\nLed ranking model", "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    session_id = response.headers["location"].split("/sessions/")[1]
    user_md = (output_dir / "sessions" / session_id / "USER.md").read_text(encoding="utf8")
    assert "Led ranking model" in user_md
    assert "jane@example.com" not in user_md
    assert (output_dir / "sessions" / session_id / "artifacts" / "profile" / "resume.txt").exists()
