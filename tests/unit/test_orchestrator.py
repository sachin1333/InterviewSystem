"""Smoke tests for the orchestrator. Exhaustive FSM coverage lives in test_invariants.py."""
from datetime import UTC, datetime

from core.events import Envelope, SessionEnded, SessionStarted
from core.orchestrator import NoAction, next_action
from core.projections import ScoreStore, SessionStore


def test_unknown_session_returns_no_action():
    a = next_action("missing", SessionStore(), ScoreStore())
    assert isinstance(a, NoAction) and a.reason == "unknown-session"


def test_ended_session_returns_no_action():
    sessions = SessionStore()
    e1 = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,tzinfo=UTC),
                  payload=SessionStarted(rubric_version="v1"))
    e2 = Envelope(session_id="s1", seq=2, at=datetime(2026,4,22,tzinfo=UTC),
                  payload=SessionEnded(reason="done"))
    sessions.apply(e1)
    sessions.apply(e2)
    a = next_action("s1", sessions, ScoreStore())
    assert isinstance(a, NoAction) and a.reason == "session-ended"


def test_next_action_is_pure():
    sessions = SessionStore()
    e = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,tzinfo=UTC),
                 payload=SessionStarted(rubric_version="v1"))
    sessions.apply(e)
    a1 = next_action("s1", sessions, ScoreStore())
    a2 = next_action("s1", sessions, ScoreStore())
    assert a1 == a2
