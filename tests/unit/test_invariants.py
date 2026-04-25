"""Row-by-row FSM coverage for `core/invariants.md`.

Each test here corresponds to exactly one row of the state-transition table.
If invariants.md changes, update or add tests here to match.
"""
from datetime import UTC, datetime, timedelta

from core.domain import Actor, ArtifactKind, Dimension, Score, Signal, TurnKind
from core.events import (
    ArtifactAttached,
    Envelope,
    RuntimeExecuted,
    ScoreComputed,
    ScorerFailed,
    SessionEnded,
    SessionStarted,
    SignalEmitted,
    TurnPosted,
)
from core.orchestrator import (
    EndSession,
    NoAction,
    RequestAggregate,
    RequestCandidateInput,
    RequestChallenge,
    RequestExecution,
    RequestProbe,
    RequestScoring,
    next_action,
)
from core.projections import RuntimeStore, ScoreStore, SessionStore, SignalStore

BASE = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


def _env(session_id, seq, payload, offset_s=0):
    return Envelope(session_id=session_id, seq=seq,
                    at=BASE + timedelta(seconds=offset_s), payload=payload)


def _apply_all(envelopes, *stores):
    for ev in envelopes:
        for store in stores:
            store.apply(ev)


# --- row 1: unstarted ----------------------------------------------------

def test_row1_unstarted_no_action():
    a = next_action("nope", SessionStore(), ScoreStore())
    assert isinstance(a, NoAction) and a.reason == "unknown-session"


# --- row 2: ended --------------------------------------------------------

def test_row2_ended_no_action():
    sessions = SessionStore()
    _apply_all([
        _env("s", 1, SessionStarted(rubric_version="v1")),
        _env("s", 2, SessionEnded(reason="done")),
    ], sessions)
    a = next_action("s", sessions, ScoreStore())
    assert isinstance(a, NoAction) and a.reason == "session-ended"


# --- row 3: scored → EndSession -----------------------------------------

def test_row3_scored_ends_session():
    sessions = SessionStore()
    scores = ScoreStore()
    _apply_all([_env("s", 1, SessionStarted(rubric_version="v1"))], sessions)
    sc = Score(
        session_id="s", rubric_version="v1",
        per_dimension={Dimension.communication: 0.7}, composite=0.7, at=BASE,
    )
    _apply_all([_env("s", 2, ScoreComputed(score=sc))], scores)
    a = next_action("s", sessions, scores)
    assert isinstance(a, EndSession) and a.session_id == "s"


# --- row 4: no prompt → RequestChallenge -------------------------------

def test_row4_no_prompt_requests_challenge():
    sessions = SessionStore()
    _apply_all([_env("s", 1, SessionStarted(rubric_version="v1"))], sessions)
    a = next_action("s", sessions, ScoreStore())
    assert isinstance(a, RequestChallenge) and a.session_id == "s"


# --- row 5: answer with unrun code cell → RequestExecution --------------

def test_row5_unrun_code_cell_requests_execution():
    sessions = SessionStore()
    runtimes = RuntimeStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1")),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
        _env("s", 4, ArtifactAttached(id="code1", kind=ArtifactKind.code_cell,
                                       produced_by_turn_id="a1", version=1)),
    ]
    _apply_all(events, sessions, runtimes)
    a = next_action("s", sessions, ScoreStore(), runtimes=runtimes)
    assert isinstance(a, RequestExecution)
    assert a.turn_id == "a1" and a.artifact_id == "code1"


def test_row5_executed_code_does_not_re_request():
    sessions = SessionStore()
    runtimes = RuntimeStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1")),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
        _env("s", 4, ArtifactAttached(id="code1", kind=ArtifactKind.code_cell,
                                       produced_by_turn_id="a1", version=1)),
        _env("s", 5, RuntimeExecuted(turn_id="a1", exit_code=0, wall_ms=50,
                                      artifact_id="out1")),
    ]
    _apply_all(events, sessions, runtimes)
    a = next_action("s", sessions, ScoreStore(), runtimes=runtimes)
    # runtime done → fall through to probe/advance
    assert not isinstance(a, RequestExecution)


# --- row 6: after probe → RequestCandidateInput(kind=defense) ----------

def test_row6_probe_expects_defense():
    sessions = SessionStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1")),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
        _env("s", 4, TurnPosted(id="p1", actor=Actor.examiner, kind=TurnKind.probe)),
    ]
    _apply_all(events, sessions)
    a = next_action("s", sessions, ScoreStore())
    assert isinstance(a, RequestCandidateInput) and a.kind == TurnKind.defense


# --- row 7: after prompt, no answer → RequestCandidateInput(kind=answer)

def test_row7_prompt_expects_answer():
    sessions = SessionStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1")),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
    ]
    _apply_all(events, sessions)
    a = next_action("s", sessions, ScoreStore())
    assert isinstance(a, RequestCandidateInput) and a.kind == TurnKind.answer


# --- row 8: answer with room for more probes → RequestProbe ------------

def test_row8_answer_triggers_probe():
    sessions = SessionStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1", target_answers=3,
                                     max_probes_per_prompt=1)),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
    ]
    _apply_all(events, sessions)
    a = next_action("s", sessions, ScoreStore())
    assert isinstance(a, RequestProbe)


# --- row 9a: probes exhausted, target not reached → new challenge ------

def test_row9a_probes_exhausted_advances_to_next_challenge():
    sessions = SessionStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1", target_answers=3,
                                     max_probes_per_prompt=1)),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
        _env("s", 4, TurnPosted(id="p1", actor=Actor.examiner, kind=TurnKind.probe)),
        _env("s", 5, TurnPosted(id="d1", actor=Actor.candidate, kind=TurnKind.defense)),
    ]
    _apply_all(events, sessions)
    a = next_action("s", sessions, ScoreStore())
    assert isinstance(a, RequestChallenge)


# --- row 9b / 10: target reached → scoring phase ----------------------

def test_row10_target_reached_requests_scoring():
    sessions = SessionStore()
    signals = SignalStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1", target_answers=1,
                                     max_probes_per_prompt=0)),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
        _env("s", 4, ArtifactAttached(id="md1", kind=ArtifactKind.markdown,
                                       produced_by_turn_id="a1", version=1)),
    ]
    _apply_all(events, sessions, signals)
    a = next_action("s", sessions, ScoreStore(), signals=signals)
    assert isinstance(a, RequestScoring)
    assert a.artifact_id == "md1"


# --- row 12: all dims touched → RequestAggregate -----------------------

def test_row12_all_dims_touched_requests_aggregate():
    sessions = SessionStore()
    signals = SignalStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1", target_answers=1,
                                     max_probes_per_prompt=0)),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
        _env("s", 4, ArtifactAttached(id="md1", kind=ArtifactKind.markdown,
                                       produced_by_turn_id="a1", version=1)),
    ]
    # emit a signal OR failure for every scored dimension
    dims = (
        Dimension.problem_framing,
        Dimension.model_rationale,
        Dimension.insight_interp,
        Dimension.communication,
    )
    seq = 5
    for d in dims:
        sig = Signal(
            dimension=d, value=0.5, confidence=0.5,
            source_refs=("md1",), emitted_by="fake_scorer", at=BASE,
        )
        events.append(_env("s", seq, SignalEmitted(signal=sig)))
        seq += 1

    _apply_all(events, sessions, signals)
    a = next_action("s", sessions, ScoreStore(), signals=signals)
    assert isinstance(a, RequestAggregate)


def test_row12_scorer_failed_also_counts_as_touched():
    sessions = SessionStore()
    signals = SignalStore()
    events = [
        _env("s", 1, SessionStarted(rubric_version="v1", target_answers=1,
                                     max_probes_per_prompt=0)),
        _env("s", 2, TurnPosted(id="q1", actor=Actor.challenger, kind=TurnKind.question)),
        _env("s", 3, TurnPosted(id="a1", actor=Actor.candidate, kind=TurnKind.answer)),
        _env("s", 4, ArtifactAttached(id="md1", kind=ArtifactKind.markdown,
                                       produced_by_turn_id="a1", version=1)),
    ]
    dims = (
        Dimension.problem_framing,
        Dimension.model_rationale,
        Dimension.insight_interp,
        Dimension.communication,
    )
    seq = 5
    for d in dims:
        sig = Signal(
            dimension=d, value=0.3, confidence=0.3,
            source_refs=("md1",), emitted_by="fake", at=BASE,
        )
        events.append(_env("s", seq, SignalEmitted(signal=sig)))
        seq += 1
    # also add a ScorerFailed — must not break aggregation logic
    events.append(_env("s", seq, ScorerFailed(
        dimension=Dimension.communication, reason="llm_timeout")))

    _apply_all(events, sessions, signals)
    a = next_action("s", sessions, ScoreStore(), signals=signals)
    assert isinstance(a, RequestAggregate)
