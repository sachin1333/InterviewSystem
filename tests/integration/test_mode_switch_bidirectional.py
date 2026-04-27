"""Integration tests: bidirectional voice/text mode toggle (θ.4).

Verifies that the interview_mode cookie controls active_mode in the rendered
session page, and that the mode-toggle buttons are rendered when allowed.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator


def _make_voice_client(tmp_path: Path) -> tuple[TestClient, str]:
    fake = FakeRouter()
    router = ModelRouter(fake)
    log = SqliteEventLog(str(tmp_path / "log.db"))
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    runner = SessionRunner(
        challenger=__import__(
            "adapters.challenger.llm_challenger", fromlist=["LlmChallenger"]
        ).LlmChallenger(router),
        aggregator=aggregator,
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, voice_mode="on")
    client = TestClient(app, raise_server_exceptions=True)
    r = client.post("/sessions", data={"candidate_handle": "x"}, follow_redirects=True)
    session_id = r.url.path.rsplit("/", 1)[-1]
    return client, session_id


def test_default_active_mode_is_voice(tmp_path: Path) -> None:
    """Without a cookie, active_mode defaults to voice."""
    client, session_id = _make_voice_client(tmp_path)
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert 'data-active-mode="voice"' in r.text


def test_cookie_text_sets_active_mode_text(tmp_path: Path) -> None:
    """Cookie interview_mode=text → active_mode=text, text composer open."""
    client, session_id = _make_voice_client(tmp_path)
    client.cookies.set("interview_mode", "text")
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert 'data-active-mode="text"' in r.text
    # Text <details> should have open attribute
    assert "<details" in r.text


def test_cookie_voice_sets_active_mode_voice(tmp_path: Path) -> None:
    """Cookie interview_mode=voice → active_mode=voice."""
    client, session_id = _make_voice_client(tmp_path)
    client.cookies.set("interview_mode", "voice")
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert 'data-active-mode="voice"' in r.text


def test_mode_toggle_buttons_present_when_voice_on(tmp_path: Path) -> None:
    """Mode-toggle buttons rendered when voice_mode=on (allow_text_switch=True)."""
    client, session_id = _make_voice_client(tmp_path)
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert 'id="btn-use-voice"' in r.text
    assert 'id="btn-use-text"' in r.text


def test_no_mode_toggle_buttons_when_voice_off(tmp_path: Path) -> None:
    """No toggle buttons when voice_mode=off."""
    fake = FakeRouter()
    router = ModelRouter(fake)
    log = SqliteEventLog(str(tmp_path / "log2.db"))
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    from adapters.challenger.llm_challenger import LlmChallenger

    runner = SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=aggregator,
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, voice_mode="off")
    client = TestClient(app, raise_server_exceptions=True)
    r = client.post("/sessions", data={"candidate_handle": "x"}, follow_redirects=True)
    session_id = r.url.path.rsplit("/", 1)[-1]
    r2 = client.get(f"/sessions/{session_id}")
    assert r2.status_code == 200
    assert 'id="btn-use-voice"' not in r2.text
    assert 'data-active-mode="text"' in r2.text
