"""Boot a session from a persistent :class:`EventLog` into fresh projections.

Cold-start path for the web harness:

1. Open log, stream events for `session_id` in seq order.
2. Apply each envelope to every projection store.
3. If any prior events existed, append a :class:`SessionResumed` event to the
   log itself so downstream analytics can distinguish the first instance of
   the process from a resumed one. The resumed event is also applied so the
   in-memory projection matches the on-disk log.

Returns the fully-populated stores so the caller can drive `next_action()`
immediately.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.contracts import EventLog
from core.events import Envelope, SessionResumed
from core.projections import (
    ArtifactStore,
    RuntimeStore,
    ScoreStore,
    SessionStore,
    SignalStore,
    StageStore,
)


def boot(
    session_id: str,
    log: EventLog,
) -> tuple[SessionStore, ScoreStore, SignalStore, RuntimeStore]:
    """Replay all events for `session_id` from `log` into fresh projections.

    If at least one event exists for the session, a ``SessionResumed(from_seq)``
    event is appended to the log (and applied) so resume events are durable
    and auditable.
    """
    sessions = SessionStore()
    scores = ScoreStore()
    signals = SignalStore()
    runtimes = RuntimeStore()

    envelopes = log.get_session(session_id)
    for env in envelopes:
        sessions.apply(env)
        scores.apply(env)
        signals.apply(env)
        runtimes.apply(env)

    if envelopes:
        last_seq = envelopes[-1].seq
        resumed = Envelope(
            session_id=session_id,
            seq=last_seq + 1,
            at=datetime.now(UTC),
            payload=SessionResumed(from_seq=last_seq),
        )
        persisted = log.append(resumed)
        sessions.apply(persisted)
        scores.apply(persisted)
        signals.apply(persisted)
        runtimes.apply(persisted)

    return sessions, scores, signals, runtimes


def replay(
    session_id: str,
    log: EventLog,
) -> tuple[SessionStore, ScoreStore, SignalStore, RuntimeStore, ArtifactStore]:
    """Replay all events for `session_id` into fresh projections — no side effects.

    Unlike :func:`boot`, this never appends events to the log. Use it for
    read-only projection rebuilds (e.g., every HTTP GET request).
    """
    sessions = SessionStore()
    scores = ScoreStore()
    signals = SignalStore()
    runtimes = RuntimeStore()
    artifacts = ArtifactStore()

    for env in log.get_session(session_id):
        sessions.apply(env)
        scores.apply(env)
        signals.apply(env)
        runtimes.apply(env)
        artifacts.apply(env)

    return sessions, scores, signals, runtimes, artifacts


def replay_with_stages(
    session_id: str,
    log: EventLog,
    *,
    stage_sequence: tuple[object, ...] = (),
) -> tuple[SessionStore, ScoreStore, SignalStore, RuntimeStore, ArtifactStore, StageStore]:
    """Like :func:`replay` but also populates a :class:`StageStore`.

    Pass ``stage_sequence`` (a tuple of CaseStage) to enable stage navigation;
    omit it for a bare store that only tracks IDs.
    """
    sessions = SessionStore()
    scores = ScoreStore()
    signals = SignalStore()
    runtimes = RuntimeStore()
    artifacts = ArtifactStore()
    stages = StageStore(stage_sequence)

    for env in log.get_session(session_id):
        sessions.apply(env)
        scores.apply(env)
        signals.apply(env)
        runtimes.apply(env)
        artifacts.apply(env)
        stages.apply(env)

    return sessions, scores, signals, runtimes, artifacts, stages
