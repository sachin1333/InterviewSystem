"""Phase 2.1 unit tests.

Covers:
  2.1.11 – Session with three problems: event ordering invariants,
            projection correctness on ProblemIntroduced/ProblemClosed.
  2.1.12 – Replay of a legacy Phase α session with StageEntered/StageCompleted
            events replays cleanly under the new orchestrator without errors.

Tests are pure (no I/O): they build synthetic event logs in memory.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.domain import Dimension, Problem, ProblemId
from core.eventlog import InMemoryEventLog
from core.events import (
    ArtifactAttached,
    CandidateJoined,
    Envelope,
    ProblemClosed,
    ProblemIntroduced,
    SessionEnded,
    SessionStarted,
    StageCompleted,
    StageEntered,
    TurnPosted,
)
from core.domain import Actor, ArtifactKind, TurnKind
from core.problem_sequencer import EndSession, IntroduceNext, Noop, ProblemSequencer
from core.projections import SessionStore, _ProblemStatus
from core.session_boot import replay


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pid(n: int) -> ProblemId:
    return ProblemId(f"p{n}")


def _make_problem(n: int) -> Problem:
    return Problem(
        id=_pid(n),
        opener_text=f"Question {n}: describe your approach.",
        context=f"Context for problem {n}.",
        target_dimensions=(Dimension.problem_framing, Dimension.communication),
        dim_thresholds={Dimension.problem_framing: 0.6, Dimension.communication: 0.6},
        expected_duration_s=300,
    )


def _ts(offset: int = 0) -> datetime:
    return datetime(2026, 4, 28, 10, 0, offset, tzinfo=UTC)


def _append_seq(log: InMemoryEventLog, session_id: str, *payloads: object) -> None:
    """Append multiple payloads in sequence to *log*."""
    for payload in payloads:
        seq = (log.last_seq(session_id) or 0) + 1
        env = Envelope(
            session_id=session_id,
            seq=seq,
            at=_ts(seq),
            payload=payload,
        )
        log.append(env)


# ---------------------------------------------------------------------------
# 2.1.1 — Problem dataclass construction
# ---------------------------------------------------------------------------

def test_problem_dataclass_fields():
    p = _make_problem(1)
    assert p.id == "p1"
    assert p.opener_text == "Question 1: describe your approach."
    assert p.expected_duration_s == 300
    assert Dimension.problem_framing in p.target_dimensions
    assert p.dim_thresholds[Dimension.problem_framing] == 0.6


def test_problem_default_fields():
    p = Problem(id=ProblemId("minimal"), opener_text="Hello?")
    assert p.context == ""
    assert p.target_dimensions == ()
    assert p.dim_thresholds == {}
    assert p.expected_duration_s == 300


# ---------------------------------------------------------------------------
# 2.1.2/3 — ProblemIntroduced / ProblemClosed event construction
# ---------------------------------------------------------------------------

def test_problem_introduced_fields():
    evt = ProblemIntroduced(problem_id=_pid(1), opener_text="Q1", ordinal=1)
    assert evt.problem_id == "p1"
    assert evt.ordinal == 1


def test_problem_closed_valid_reasons():
    for reason in ("coverage_saturated", "time_capped", "examiner_pivot", "max_probes"):
        pc = ProblemClosed(problem_id=_pid(1), reason=reason, rationale="done")
        assert pc.reason == reason


def test_problem_closed_invalid_reason():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProblemClosed(problem_id=_pid(1), reason="unknown_reason")


def test_problem_introduced_ordinal_ge_1():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProblemIntroduced(problem_id=_pid(1), opener_text="x", ordinal=0)


# ---------------------------------------------------------------------------
# 2.1.4 — SessionStore projection with ProblemIntroduced / ProblemClosed
# ---------------------------------------------------------------------------

def test_session_store_tracks_first_problem_introduced():
    log = InMemoryEventLog()
    sid = "ses-proj-01"
    _append_seq(
        log, sid,
        SessionStarted(rubric_version="v1"),
        ProblemIntroduced(problem_id=_pid(1), opener_text="Q1", ordinal=1),
    )
    store = SessionStore()
    for env in log.get_session(sid):
        store.apply(env)
    rec = store.get(sid)
    assert rec is not None
    assert rec["current_problem_id"] == "p1"
    assert rec["problem_status"]["p1"] == _ProblemStatus.active
    assert len(rec["problems"]) == 1
    assert rec["problems"][0].id == "p1"


def test_session_store_tracks_problem_closed():
    log = InMemoryEventLog()
    sid = "ses-proj-02"
    _append_seq(
        log, sid,
        SessionStarted(rubric_version="v1"),
        ProblemIntroduced(problem_id=_pid(1), opener_text="Q1", ordinal=1),
        ProblemClosed(problem_id=_pid(1), reason="coverage_saturated"),
    )
    store = SessionStore()
    for env in log.get_session(sid):
        store.apply(env)
    rec = store.get(sid)
    assert rec["current_problem_id"] is None
    assert rec["problem_status"]["p1"] == _ProblemStatus.closed


def test_session_store_three_problems_full_sequence():
    """2.1.11 core: three problems introduced + closed, projection correctness."""
    log = InMemoryEventLog()
    sid = "ses-3prob"
    _append_seq(
        log, sid,
        SessionStarted(rubric_version="v1"),
        # Problem 1
        ProblemIntroduced(problem_id=_pid(1), opener_text="Q1", ordinal=1),
        ProblemClosed(problem_id=_pid(1), reason="coverage_saturated"),
        # Problem 2
        ProblemIntroduced(problem_id=_pid(2), opener_text="Q2", ordinal=2),
        ProblemClosed(problem_id=_pid(2), reason="time_capped"),
        # Problem 3
        ProblemIntroduced(problem_id=_pid(3), opener_text="Q3", ordinal=3),
        ProblemClosed(problem_id=_pid(3), reason="max_probes"),
        SessionEnded(reason="all_problems_closed"),
    )
    store = SessionStore()
    for env in log.get_session(sid):
        store.apply(env)
    rec = store.get(sid)
    assert len(rec["problems"]) == 3
    assert rec["current_problem_id"] is None
    assert all(
        rec["problem_status"][f"p{n}"] == _ProblemStatus.closed for n in (1, 2, 3)
    )
    assert rec["ended"] is True


def test_session_store_problem_introduced_is_idempotent_on_replay():
    """Replaying the same ProblemIntroduced twice must not double-add the problem."""
    log = InMemoryEventLog()
    sid = "ses-idem"
    _append_seq(
        log, sid,
        SessionStarted(rubric_version="v1"),
        ProblemIntroduced(problem_id=_pid(1), opener_text="Q1", ordinal=1),
    )
    store = SessionStore()
    # Replay twice (simulates a resume + second replay)
    for env in log.get_session(sid):
        store.apply(env)
    for env in log.get_session(sid):
        store.apply(env)
    rec = store.get(sid)
    # Must still be exactly one Problem in the list
    assert len(rec["problems"]) == 1


# ---------------------------------------------------------------------------
# 2.1.6 — FSM ordering invariants via ProblemSequencer
# ---------------------------------------------------------------------------

def _record_with_status(statuses: dict[str, str], current: str | None) -> dict:
    """Build a minimal _SessionRecord-like dict for sequencer tests."""
    from core.projections import _ProblemStatus
    return {
        "problem_status": {ProblemId(k): _ProblemStatus(v) for k, v in statuses.items()},
        "current_problem_id": ProblemId(current) if current else None,
        "problems": [],
        "turns": [],
        "turn_ids": (),
        "artifact_ids": (),
        "artifact_kind": {},
        "artifact_turn": {},
        "pack_id": "ds-ml-v1",
        "rubric_version": "v1",
        "started_at": _ts(),
        "ended": False,
        "target_answers": 3,
        "max_probes_per_prompt": 1,
    }


def test_sequencer_introduces_first_problem_when_none_active():
    problems = [_make_problem(1), _make_problem(2), _make_problem(3)]
    seq = ProblemSequencer(problems)
    rec = _record_with_status({}, None)
    action = seq.next_event(rec)
    assert isinstance(action, IntroduceNext)
    assert action.problem.id == "p1"
    assert action.ordinal == 1


def test_sequencer_noop_while_problem_active():
    problems = [_make_problem(1), _make_problem(2)]
    seq = ProblemSequencer(problems)
    rec = _record_with_status({"p1": "active"}, "p1")
    action = seq.next_event(rec)
    assert isinstance(action, Noop)


def test_sequencer_introduces_second_after_first_closed():
    problems = [_make_problem(1), _make_problem(2), _make_problem(3)]
    seq = ProblemSequencer(problems)
    rec = _record_with_status({"p1": "closed"}, None)
    action = seq.next_event(rec)
    assert isinstance(action, IntroduceNext)
    assert action.problem.id == "p2"
    assert action.ordinal == 2


def test_sequencer_ends_session_when_all_problems_closed():
    problems = [_make_problem(1), _make_problem(2)]
    seq = ProblemSequencer(problems)
    rec = _record_with_status({"p1": "closed", "p2": "closed"}, None)
    action = seq.next_event(rec)
    assert isinstance(action, EndSession)


def test_sequencer_noop_for_empty_problem_list():
    seq = ProblemSequencer([])
    rec = _record_with_status({}, None)
    action = seq.next_event(rec)
    assert isinstance(action, Noop)
    assert action.reason == "no_problems_planned"


def test_sequencer_validate_introduce_rejects_unknown_problem():
    seq = ProblemSequencer([_make_problem(1)])
    rec = _record_with_status({}, None)
    with pytest.raises(ValueError, match="not in planned list"):
        seq.validate_introduce(_pid(99), rec)


def test_sequencer_validate_introduce_rejects_while_active():
    seq = ProblemSequencer([_make_problem(1), _make_problem(2)])
    rec = _record_with_status({"p1": "active"}, "p1")
    with pytest.raises(ValueError, match="still active"):
        seq.validate_introduce(_pid(2), rec)


def test_sequencer_validate_introduce_rejects_duplicate():
    seq = ProblemSequencer([_make_problem(1)])
    rec = _record_with_status({"p1": "active"}, "p1")
    with pytest.raises(ValueError):
        seq.validate_introduce(_pid(1), rec)


def test_sequencer_validate_close_rejects_unopened():
    seq = ProblemSequencer([_make_problem(1)])
    rec = _record_with_status({}, None)
    with pytest.raises(ValueError, match="never introduced"):
        seq.validate_close(_pid(1), rec)


def test_sequencer_validate_close_rejects_already_closed():
    seq = ProblemSequencer([_make_problem(1)])
    rec = _record_with_status({"p1": "closed"}, None)
    with pytest.raises(ValueError, match="already closed"):
        seq.validate_close(_pid(1), rec)


# ---------------------------------------------------------------------------
# 2.1.12 — Replay of a legacy Phase α session (StageEntered/StageCompleted)
# ---------------------------------------------------------------------------

def test_legacy_stage_events_replay_cleanly():
    """A session that only contains StageEntered/StageCompleted events
    (no ProblemIntroduced/ProblemClosed) must replay without errors and
    produce an empty problem list in the projection.
    """
    log = InMemoryEventLog()
    sid = "ses-legacy-alpha"
    _append_seq(
        log, sid,
        SessionStarted(rubric_version="v1"),
        CandidateJoined(candidate_handle="alice"),
        StageEntered(stage_id="s1", primitive="open_ended"),
        TurnPosted(id="t1", actor=Actor.challenger, kind=TurnKind.question),
        ArtifactAttached(
            id="a1", kind=ArtifactKind.prompt, produced_by_turn_id="t1",
            version=1, content="Describe your last ML project.",
        ),
        TurnPosted(id="t2", actor=Actor.candidate, kind=TurnKind.answer),
        ArtifactAttached(
            id="a2", kind=ArtifactKind.markdown, produced_by_turn_id="t2",
            version=1, content="I built a churn model.",
        ),
        StageCompleted(stage_id="s1"),
        SessionEnded(reason="scored"),
    )

    # Replay must not raise.
    sessions, scores, signals, runtimes, artifacts = replay(sid, log)

    rec = sessions.get(sid)
    assert rec is not None
    assert rec["ended"] is True
    # No problem events in this log — projection must be empty.
    assert rec["problems"] == []
    assert rec["current_problem_id"] is None
    assert rec["problem_status"] == {}
    # Turns and artifacts still tracked as before.
    assert len(rec["turns"]) == 2
    assert "a1" in rec["artifact_ids"]
    assert "a2" in rec["artifact_ids"]


def test_legacy_session_replay_does_not_raise_on_stage_events_in_sqlite():
    """SQLite round-trip: StageEntered/StageCompleted rows survive serialise/deserialise."""
    import tempfile, os
    from adapters.eventlog.sqlite_log import SqliteEventLog

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        with SqliteEventLog(db_path) as db:
            sid = "ses-sqlite-legacy"
            for payload in [
                SessionStarted(rubric_version="v1"),
                StageEntered(stage_id="s1", primitive="open_ended"),
                StageCompleted(stage_id="s1"),
                ProblemIntroduced(problem_id=_pid(1), opener_text="Q1", ordinal=1),
                ProblemClosed(problem_id=_pid(1), reason="coverage_saturated"),
            ]:
                seq = (db.last_seq(sid) or 0) + 1
                db.append(Envelope(
                    session_id=sid, seq=seq, at=_ts(seq), payload=payload,
                ))

            envelopes = db.get_session(sid)
        # Deserialization must not raise; all 5 events must survive.
        assert len(envelopes) == 5
        types = [type(e.payload).__name__ for e in envelopes]
        assert "StageEntered" in types
        assert "ProblemIntroduced" in types
        assert "ProblemClosed" in types
    finally:
        os.unlink(db_path)
