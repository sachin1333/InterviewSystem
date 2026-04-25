"""End-to-end golden sequence: event stream drives orchestrator through every
major FSM transition until a composite score is computed and the session ends.

This is the "prove it" gate for Phase alpha: the FSM can run a full interview
from SessionStarted -> ScoreComputed -> EndSession without hand-holding.
"""
from datetime import UTC, datetime, timedelta

from core.domain import Actor, ArtifactKind, Dimension, Score, Signal, TurnKind
from core.eventlog import InMemoryEventLog
from core.events import (
    ArtifactAttached,
    Envelope,
    ScoreComputed,
    SessionStarted,
    SignalEmitted,
    TurnPosted,
)
from core.orchestrator import (
    EndSession,
    RequestAggregate,
    RequestCandidateInput,
    RequestChallenge,
    RequestProbe,
    RequestScoring,
    next_action,
)
from core.projections import RuntimeStore, ScoreStore, SessionStore, SignalStore

BASE = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


def _env(session_id: str, seq: int, payload, offset_s: int = 0) -> Envelope:
    return Envelope(session_id=session_id, seq=seq,
                    at=BASE + timedelta(seconds=offset_s), payload=payload)


def test_golden_sequence_drives_full_interview():
    sid = "golden"
    log = InMemoryEventLog()
    sessions = SessionStore()
    scores = ScoreStore()
    signals = SignalStore()
    runtimes = RuntimeStore()

    def append_and_apply(env: Envelope) -> None:
        log.append(env)
        for store in (sessions, scores, signals, runtimes):
            store.apply(env)

    # --- session starts with minimal policy for a short test ------------
    append_and_apply(_env(sid, 1, SessionStarted(
        rubric_version="ds_mle@1", target_answers=1, max_probes_per_prompt=1,
    )))

    # FSM row 4: no prompt → RequestChallenge
    assert isinstance(next_action(sid, sessions, scores, signals, runtimes), RequestChallenge)

    # challenger posts a question turn
    append_and_apply(_env(sid, 2, TurnPosted(
        id="q1", actor=Actor.challenger, kind=TurnKind.question)))

    # FSM row 7: prompt exists → RequestCandidateInput(kind=answer)
    a = next_action(sid, sessions, scores, signals, runtimes)
    assert isinstance(a, RequestCandidateInput) and a.kind == TurnKind.answer

    # candidate answers
    append_and_apply(_env(sid, 3, TurnPosted(
        id="a1", actor=Actor.candidate, kind=TurnKind.answer)))
    append_and_apply(_env(sid, 4, ArtifactAttached(
        id="md1", kind=ArtifactKind.markdown, produced_by_turn_id="a1", version=1)))

    # FSM row 8: answer → RequestProbe (target=1 not yet reached; 0 probes so far)
    assert isinstance(next_action(sid, sessions, scores, signals, runtimes), RequestProbe)

    # examiner probes
    append_and_apply(_env(sid, 5, TurnPosted(
        id="p1", actor=Actor.examiner, kind=TurnKind.probe)))

    # FSM row 6: probe posted → RequestCandidateInput(kind=defense)
    a = next_action(sid, sessions, scores, signals, runtimes)
    assert isinstance(a, RequestCandidateInput) and a.kind == TurnKind.defense

    # candidate defends (this becomes a defense turn, counts are: 1 answer, 1 defense)
    append_and_apply(_env(sid, 6, TurnPosted(
        id="d1", actor=Actor.candidate, kind=TurnKind.defense)))

    # With target_answers=1 reached (1 answer) AND probes exhausted for this prompt,
    # FSM row 10: target reached → RequestScoring for first (artifact, dim) pair
    a = next_action(sid, sessions, scores, signals, runtimes)
    assert isinstance(a, RequestScoring)

    # emit signals for every scored dimension against md1
    dims = (
        Dimension.problem_framing,
        Dimension.model_rationale,
        Dimension.insight_interp,
        Dimension.communication,
    )
    seq = 7
    for d in dims:
        sig = Signal(
            dimension=d, value=0.6, confidence=0.8,
            source_refs=("md1",), emitted_by="fake_scorer", at=BASE,
        )
        append_and_apply(_env(sid, seq, SignalEmitted(signal=sig)))
        seq += 1

    # FSM row 12: all dims touched → RequestAggregate
    assert isinstance(next_action(sid, sessions, scores, signals, runtimes), RequestAggregate)

    # aggregator produces a composite score
    composite = 0.6
    sc = Score(
        session_id=sid, rubric_version="ds_mle@1",
        per_dimension={d: 0.6 for d in dims},
        composite=composite, at=BASE,
    )
    append_and_apply(_env(sid, seq, ScoreComputed(score=sc)))

    # FSM row 3: scored → EndSession
    a = next_action(sid, sessions, scores, signals, runtimes)
    assert isinstance(a, EndSession) and a.reason == "scored"
