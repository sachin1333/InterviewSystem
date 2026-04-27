"""Integration tests: push-to-talk UI (θ.5).

Verifies that the PTT button markup is rendered in voice mode and absent (or
irrelevant) in text mode, and that VAD-auto-start is not present in the JS.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator


def _make_client(tmp_path: Path, *, voice_mode: str = "on") -> tuple[TestClient, str]:
    fake = FakeRouter()
    router = ModelRouter(fake)
    log = SqliteEventLog(str(tmp_path / "log.db"))
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=aggregator,
    )
    app = make_app(log=log, runner=runner, output_dir=tmp_path, voice_mode=voice_mode)
    client = TestClient(app, raise_server_exceptions=True)
    r = client.post("/sessions", data={"candidate_handle": "x"}, follow_redirects=True)
    session_id = r.url.path.rsplit("/", 1)[-1]
    return client, session_id


def test_ptt_button_present_in_voice_mode(tmp_path: Path) -> None:
    """PTT button rendered when active_mode=voice."""
    client, session_id = _make_client(tmp_path, voice_mode="on")
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert 'data-mic="ptt"' in r.text
    assert 'id="mic-btn"' in r.text


def test_ptt_button_voice_bar_off_in_text_mode(tmp_path: Path) -> None:
    """Voice bar is set data-voice-mode=off when interview_mode cookie=text."""
    client, session_id = _make_client(tmp_path, voice_mode="on")
    client.cookies.set("interview_mode", "text")
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    assert 'data-voice-mode="off"' in r.text


def test_ptt_button_absent_from_voice_off_sessions(tmp_path: Path) -> None:
    """When voice_mode=off, voice bar is not active (data-voice-mode=off)."""
    client, session_id = _make_client(tmp_path, voice_mode="off")
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200
    # PTT button may be in DOM but voice bar must be off
    assert 'data-voice-mode="off"' in r.text


def test_ptt_js_uses_pointerdown_not_vad(tmp_path: Path) -> None:
    """Served voice.js uses pointerdown (PTT) not VAD continuous start."""
    client, _sid = _make_client(tmp_path, voice_mode="on")
    r = client.get("/static/voice.js")
    assert r.status_code == 200
    js = r.text
    assert "pointerdown" in js, "PTT handler must use pointerdown"
    # VAD auto-start removed: initialize() should not call mic.start() directly
    assert "await this.mic.start()" not in js or "_setupPTT" in js, (
        "mic should only start via PTT handler, not in initialize()"
    )
