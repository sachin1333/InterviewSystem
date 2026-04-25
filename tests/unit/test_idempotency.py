"""Idempotency contract across both log implementations.

Appending with the same `(session_id, idem_key)` twice MUST return the
already-stored envelope and MUST NOT write a duplicate row.
"""
from datetime import UTC, datetime

import pytest

from adapters.eventlog.sqlite_log import SqliteEventLog
from core.domain import Actor, TurnKind
from core.eventlog import InMemoryEventLog
from core.events import Envelope, SessionStarted, TurnPosted

BASE = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


def _env(seq, payload, idem_key=None, session_id="s1"):
    return Envelope(session_id=session_id, seq=seq, at=BASE, payload=payload,
                    idem_key=idem_key)


@pytest.fixture(params=["memory", "sqlite"])
def log(request, tmp_path):
    if request.param == "memory":
        yield InMemoryEventLog()
    else:
        db = SqliteEventLog(tmp_path / "log.db")
        try:
            yield db
        finally:
            db.close()


def test_duplicate_idem_key_returns_first_envelope(log):
    first = log.append(_env(1, SessionStarted(rubric_version="v1"),
                            idem_key="nonce-A"))
    # client retries with a different seq (e.g. after a crash mid-flight)
    second_attempt = _env(2, TurnPosted(id="q1", actor=Actor.challenger,
                                        kind=TurnKind.question),
                          idem_key="nonce-A")
    got = log.append(second_attempt)
    assert got.seq == first.seq
    assert got.idem_key == "nonce-A"
    assert isinstance(got.payload, SessionStarted)


def test_duplicate_idem_writes_no_second_row(log):
    log.append(_env(1, SessionStarted(rubric_version="v1"), idem_key="x"))
    log.append(_env(2, SessionStarted(rubric_version="v1"), idem_key="x"))
    rows = log.get_session("s1")
    assert len(rows) == 1


def test_idem_key_scoped_per_session(log):
    log.append(_env(1, SessionStarted(rubric_version="v1"), idem_key="shared",
                    session_id="a"))
    # same idem_key on a different session should insert a new row
    log.append(_env(1, SessionStarted(rubric_version="v1"), idem_key="shared",
                    session_id="b"))
    assert len(log.get_session("a")) == 1
    assert len(log.get_session("b")) == 1


def test_idem_key_arg_overrides_envelope(log):
    env = _env(1, SessionStarted(rubric_version="v1"), idem_key=None)
    log.append(env, idem_key="explicit")
    # retry with same explicit key should dedupe
    retry = _env(2, SessionStarted(rubric_version="v1"))
    got = log.append(retry, idem_key="explicit")
    assert got.seq == 1
    assert len(log.get_session("s1")) == 1
