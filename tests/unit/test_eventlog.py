from datetime import UTC, datetime

import pytest

from core.eventlog import InMemoryEventLog
from core.events import Envelope, SessionStarted

UTC = UTC


def test_append_and_get_session():
    log = InMemoryEventLog()
    e1 = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC), payload=SessionStarted(rubric_version="ds_mle@1"))
    e2 = Envelope(session_id="s1", seq=2, at=datetime(2026,4,22,10,0,1,tzinfo=UTC), payload=SessionStarted(rubric_version="ds_mle@1"))
    log.append(e1)
    log.append(e2)
    got = log.get_session("s1")
    assert len(got) == 2
    assert got[0].seq == 1 and got[1].seq == 2


def test_append_non_envelope_raises():
    log = InMemoryEventLog()
    with pytest.raises(TypeError):
        log.append(object())


def test_append_duplicate_seq_raises():
    log = InMemoryEventLog()
    e1 = Envelope(session_id="s2", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC), payload=SessionStarted(rubric_version="v"))
    e1b = Envelope(session_id="s2", seq=1, at=datetime(2026,4,22,10,0,1,tzinfo=UTC), payload=SessionStarted(rubric_version="v"))
    log.append(e1)
    with pytest.raises(ValueError):
        log.append(e1b)


def test_last_seq_none_when_empty():
    log = InMemoryEventLog()
    assert log.last_seq("missing") is None
