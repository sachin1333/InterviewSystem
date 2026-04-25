from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from core.domain import Actor, ArtifactKind, Dimension, Score, TurnKind
from core.events import (
    ArtifactAttached,
    Envelope,
    RuntimeExecuted,
    RuntimeFailed,
    ScoreComputed,
    ScorerFailed,
    SessionEnded,
    SessionStarted,
    SignalEmitted,
    TurnPosted,
)


class TurnInfo(TypedDict):
    id: str
    actor: Actor
    kind: TurnKind
    seq: int


class _SessionRecord(TypedDict):
    pack_id: str
    rubric_version: str | None
    started_at: datetime | None
    ended: bool
    target_answers: int
    max_probes_per_prompt: int
    # rich turn metadata
    turns: list[TurnInfo]
    # convenience projections (back-compat with earlier tests)
    turn_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    # artifact lookups
    artifact_kind: dict[str, ArtifactKind]
    artifact_turn: dict[str, str]


def _new_record() -> _SessionRecord:
    return _SessionRecord(
        pack_id="ds-ml-v1",
        rubric_version=None,
        started_at=None,
        ended=False,
        target_answers=3,
        max_probes_per_prompt=1,
        turns=[],
        turn_ids=(),
        artifact_ids=(),
        artifact_kind={},
        artifact_turn={},
    )


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRecord] = {}

    def _bucket(self, sid: str) -> _SessionRecord:
        return self._sessions.setdefault(sid, _new_record())

    def apply(self, envelope: Envelope) -> None:
        sid = envelope.session_id
        payload = envelope.payload
        if isinstance(payload, SessionStarted):
            b = self._bucket(sid)
            if b["rubric_version"] is None:
                b["pack_id"] = payload.pack_id
                b["rubric_version"] = payload.rubric_version
                b["started_at"] = envelope.at
                b["target_answers"] = payload.target_answers
                b["max_probes_per_prompt"] = payload.max_probes_per_prompt
        elif isinstance(payload, TurnPosted):
            b = self._bucket(sid)
            b["turns"].append(
                TurnInfo(id=payload.id, actor=payload.actor, kind=payload.kind, seq=envelope.seq)
            )
            b["turn_ids"] = b["turn_ids"] + (payload.id,)
        elif isinstance(payload, ArtifactAttached):
            b = self._bucket(sid)
            b["artifact_ids"] = b["artifact_ids"] + (payload.id,)
            b["artifact_kind"][payload.id] = payload.kind
            b["artifact_turn"][payload.id] = payload.produced_by_turn_id
        elif isinstance(payload, SessionEnded):
            b = self._bucket(sid)
            b["ended"] = True

    def get(self, session_id: str) -> _SessionRecord | None:
        return self._sessions.get(session_id)


class ScoreStore:
    def __init__(self) -> None:
        self._scores: dict[str, Score] = {}

    def apply(self, envelope: Envelope) -> None:
        if isinstance(envelope.payload, ScoreComputed):
            self._scores[envelope.session_id] = envelope.payload.score

    def get(self, session_id: str) -> Score | None:
        return self._scores.get(session_id)


class SignalStore:
    """Tracks per-dimension signals + per-(artifact, dimension) scoring status."""

    def __init__(self) -> None:
        # (sid, dim) -> any signal-or-failure recorded
        self._dim_touched: dict[tuple[str, Dimension], bool] = {}
        # (sid, artifact_id, dim) -> True when a scorer-derived signal was emitted for that ref
        self._artifact_dim_scored: dict[tuple[str, str, Dimension], bool] = {}

    def apply(self, envelope: Envelope) -> None:
        sid = envelope.session_id
        p = envelope.payload
        if isinstance(p, SignalEmitted):
            sig = p.signal
            self._dim_touched[(sid, sig.dimension)] = True
            for ref in sig.source_refs:
                self._artifact_dim_scored[(sid, ref, sig.dimension)] = True
        elif isinstance(p, ScorerFailed):
            self._dim_touched[(sid, p.dimension)] = True

    def has_any(self, session_id: str, dimension: Dimension) -> bool:
        return self._dim_touched.get((session_id, dimension), False)

    def is_scored(self, session_id: str, artifact_id: str, dimension: Dimension) -> bool:
        return self._artifact_dim_scored.get((session_id, artifact_id, dimension), False)


class RuntimeStore:
    """Tracks turns whose code cells have been executed (success or failure)."""

    def __init__(self) -> None:
        self._ran: set[tuple[str, str]] = set()

    def apply(self, envelope: Envelope) -> None:
        p = envelope.payload
        if isinstance(p, (RuntimeExecuted, RuntimeFailed)):
            self._ran.add((envelope.session_id, p.turn_id))

    def has_run(self, session_id: str, turn_id: str) -> bool:
        return (session_id, turn_id) in self._ran


class ArtifactStore:
    """Holds the text content of artifacts, keyed by artifact id."""

    def __init__(self) -> None:
        self._content: dict[str, str] = {}

    def apply(self, envelope: Envelope) -> None:
        p = envelope.payload
        if isinstance(p, ArtifactAttached) and p.content is not None:
            self._content[p.id] = p.content

    def get(self, artifact_id: str) -> str | None:
        return self._content.get(artifact_id)
