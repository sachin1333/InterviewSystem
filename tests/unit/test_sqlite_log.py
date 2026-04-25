"""SQLite event log: append, replay, last_seq, and cross-process-like reload."""
from datetime import UTC, datetime

import pytest

from adapters.eventlog.sqlite_log import SqliteEventLog
from core.domain import Actor, TurnKind
from core.events import Envelope, SessionStarted, TurnPosted

BASE = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


def _env(session_id, seq, payload, idem_key=None):
    return Envelope(session_id=session_id, seq=seq, at=BASE, payload=payload,
                    idem_key=idem_key)


def test_append_and_replay_roundtrips_payload(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    e1 = _env("s1", 1, SessionStarted(rubric_version="v1", target_answers=2))
    e2 = _env("s1", 2, TurnPosted(id="q1", actor=Actor.challenger,
                                   kind=TurnKind.question))
    log.append(e1)
    log.append(e2)

    got = log.get_session("s1")
    assert len(got) == 2
    assert got[0].payload == e1.payload
    assert got[1].payload == e2.payload
    assert got[0].at == e1.at  # datetime roundtrip preserves tz


def test_last_seq_tracks_session(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    assert log.last_seq("s1") is None
    log.append(_env("s1", 1, SessionStarted(rubric_version="v1")))
    assert log.last_seq("s1") == 1
    log.append(_env("s1", 5, SessionStarted(rubric_version="v1")))
    assert log.last_seq("s1") == 5
    # other session independent
    assert log.last_seq("s2") is None


def test_non_monotonic_seq_rejected(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    log.append(_env("s1", 5, SessionStarted(rubric_version="v1")))
    with pytest.raises(ValueError):
        log.append(_env("s1", 5, SessionStarted(rubric_version="v1")))
    with pytest.raises(ValueError):
        log.append(_env("s1", 3, SessionStarted(rubric_version="v1")))


def test_reopen_preserves_events(tmp_path):
    path = tmp_path / "log.db"
    log = SqliteEventLog(path)
    log.append(_env("s1", 1, SessionStarted(rubric_version="v1")))
    log.close()

    log2 = SqliteEventLog(path)
    try:
        got = log2.get_session("s1")
        assert len(got) == 1
        assert isinstance(got[0].payload, SessionStarted)
    finally:
        log2.close()


def test_all_returns_all_sessions(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    log.append(_env("s1", 1, SessionStarted(rubric_version="v1")))
    log.append(_env("s2", 1, SessionStarted(rubric_version="v1")))
    assert len(log.all()) == 2


def test_rejects_non_envelope(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    with pytest.raises(TypeError):
        log.append(SessionStarted(rubric_version="v1"))  # type: ignore[arg-type]
