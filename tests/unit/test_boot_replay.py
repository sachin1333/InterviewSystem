"""Boot path: cold-start a session from a persistent log into fresh projections.

Scenarios:
- Session with no prior events → boot returns empty stores, no SessionResumed
  appended.
- Session with prior events → projections reflect all events AND a
  SessionResumed(from_seq=last_seq) event is appended to the log.
- Replay is idempotent-ish: running `boot` twice on a clean session yields
  identical projection state (though each call does append a new
  SessionResumed event — the audit trail remembers every resume).
"""
from datetime import UTC, datetime

from adapters.eventlog.sqlite_log import SqliteEventLog
from core.domain import Actor, ArtifactKind, TurnKind
from core.eventlog import InMemoryEventLog
from core.events import (
    ArtifactAttached,
    Envelope,
    SessionResumed,
    SessionStarted,
    TurnPosted,
)
from core.orchestrator import RequestCandidateInput, next_action
from core.session_boot import boot

BASE = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


def _seed(log, sid):
    log.append(Envelope(session_id=sid, seq=1, at=BASE,
                        payload=SessionStarted(rubric_version="v1",
                                               target_answers=1,
                                               max_probes_per_prompt=0)))
    log.append(Envelope(session_id=sid, seq=2, at=BASE,
                        payload=TurnPosted(id="q1", actor=Actor.challenger,
                                           kind=TurnKind.question)))


def test_boot_empty_session_noop(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    try:
        sessions, scores, _signals, _runtimes = boot("nope", log)
        assert sessions.get("nope") is None
        assert scores.get("nope") is None
        # no SessionResumed written
        assert log.get_session("nope") == ()
    finally:
        log.close()


def test_boot_replays_all_events(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    try:
        _seed(log, "s1")
        sessions, scores, signals, runtimes = boot("s1", log)

        rec = sessions.get("s1")
        assert rec is not None
        assert rec["rubric_version"] == "v1"
        assert rec["target_answers"] == 1
        assert len(rec["turns"]) == 1
        # next_action keeps flowing from the replayed state
        action = next_action("s1", sessions, scores, signals, runtimes)
        assert isinstance(action, RequestCandidateInput)
        assert action.kind == TurnKind.answer
    finally:
        log.close()


def test_boot_appends_session_resumed(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    try:
        _seed(log, "s1")
        boot("s1", log)
        events = log.get_session("s1")
        assert len(events) == 3  # SessionStarted, TurnPosted, SessionResumed
        resumed = events[-1].payload
        assert isinstance(resumed, SessionResumed)
        assert resumed.from_seq == 2
        assert events[-1].seq == 3
    finally:
        log.close()


def test_boot_in_memory_log_also_works():
    log = InMemoryEventLog()
    _seed(log, "s1")
    sessions, _scores, _signals, _runtimes = boot("s1", log)
    assert sessions.get("s1") is not None
    assert len(log.get_session("s1")) == 3


def test_boot_preserves_artifact_projection(tmp_path):
    log = SqliteEventLog(tmp_path / "log.db")
    try:
        _seed(log, "s1")
        log.append(Envelope(session_id="s1", seq=3, at=BASE,
                            payload=TurnPosted(id="a1", actor=Actor.candidate,
                                               kind=TurnKind.answer)))
        log.append(Envelope(session_id="s1", seq=4, at=BASE,
                            payload=ArtifactAttached(id="md1",
                                                     kind=ArtifactKind.markdown,
                                                     produced_by_turn_id="a1",
                                                     version=1)))
        sessions, *_ = boot("s1", log)
        rec = sessions.get("s1")
        assert rec is not None
        assert "md1" in rec["artifact_ids"]
        assert rec["artifact_kind"]["md1"] == ArtifactKind.markdown
        assert rec["artifact_turn"]["md1"] == "a1"
    finally:
        log.close()
