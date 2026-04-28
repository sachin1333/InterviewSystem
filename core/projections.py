from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypedDict

from core.case_loader import CaseStage
from core.domain import Actor, ArtifactKind, Dimension, Problem, ProblemId, Score, TurnKind
from core.events import (
    ArtifactAttached,
    Envelope,
    PerProblemScoreComputed,
    ProblemClosed,
    ProblemIntroduced,
    RuntimeExecuted,
    RuntimeFailed,
    ScoreComputed,
    ScorerFailed,
    SessionEnded,
    SessionStarted,
    SignalEmitted,
    StageCompleted,
    StageEntered,
    TurnPosted,
)


class TurnInfo(TypedDict):
    id: str
    actor: Actor
    kind: TurnKind
    seq: int


class _ProblemStatus(StrEnum):
    active = "active"
    closed = "closed"


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
    # Phase 2.1 — problem tracking
    problems: list[Problem]
    current_problem_id: ProblemId | None
    problem_status: dict[ProblemId, _ProblemStatus]


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
        problems=[],
        current_problem_id=None,
        problem_status={},
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
        elif isinstance(payload, ProblemIntroduced):
            b = self._bucket(sid)
            pid = payload.problem_id
            # Guard: only add to list if this problem_id hasn't been seen yet
            # (idempotent replay).
            if not any(p.id == pid for p in b["problems"]):
                from core.domain import Problem  # local import avoids circular at module level
                b["problems"].append(
                    Problem(
                        id=pid,
                        opener_text=payload.opener_text,
                    )
                )
            b["current_problem_id"] = pid
            b["problem_status"][pid] = _ProblemStatus.active
        elif isinstance(payload, ProblemClosed):
            b = self._bucket(sid)
            pid = payload.problem_id
            b["problem_status"][pid] = _ProblemStatus.closed
            # Clear current_problem_id; ProblemSequencer will set it again
            # via the next ProblemIntroduced event.
            if b["current_problem_id"] == pid:
                b["current_problem_id"] = None

    def get(self, session_id: str) -> _SessionRecord | None:
        return self._sessions.get(session_id)


@dataclass(frozen=True)
class ProblemScoreInfo:
    problem_id: ProblemId
    ordinal: int
    score: Score


class ScoreStore:
    def __init__(self) -> None:
        self._scores: dict[str, Score] = {}
        self._problem_scores: dict[str, list[ProblemScoreInfo]] = {}

    def apply(self, envelope: Envelope) -> None:
        if isinstance(envelope.payload, ScoreComputed):
            self._scores[envelope.session_id] = envelope.payload.score
        elif isinstance(envelope.payload, PerProblemScoreComputed):
            payload = envelope.payload
            bucket = self._problem_scores.setdefault(envelope.session_id, [])
            bucket.append(
                ProblemScoreInfo(
                    problem_id=payload.problem_id,
                    ordinal=payload.ordinal,
                    score=payload.score,
                )
            )
            bucket.sort(key=lambda entry: entry.ordinal)

    def get(self, session_id: str) -> Score | None:
        return self._scores.get(session_id)

    def get_problem_scores(self, session_id: str) -> tuple[ProblemScoreInfo, ...]:
        return tuple(self._problem_scores.get(session_id, ()))


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


class StageStore:
    """Per-session case-stage tracker. Populated from StageEntered/StageCompleted events."""

    def __init__(self, stages: tuple[CaseStage, ...] = ()) -> None:
        self._stages = stages
        self._current: dict[str, str] = {}         # session_id -> stage_id
        self._completed: dict[str, set[str]] = {}  # session_id -> {stage_id, ...}

    def apply_for_session(self, session_id: str, evt: object) -> None:
        if isinstance(evt, StageEntered):
            self._current[session_id] = evt.stage_id
            self._completed.setdefault(session_id, set())
        elif isinstance(evt, StageCompleted):
            self._completed.setdefault(session_id, set()).add(evt.stage_id)

    def apply(self, envelope: Envelope) -> None:
        p = envelope.payload
        if isinstance(p, (StageEntered, StageCompleted)):
            self.apply_for_session(envelope.session_id, p)

    def current_id(self, session_id: str) -> str | None:
        return self._current.get(session_id)

    def completed_ids(self, session_id: str) -> set[str]:
        return set(self._completed.get(session_id, set()))

    def current(self, session_id: str) -> CaseStage | None:
        """Return the CaseStage for the current stage, or None."""
        cur_id = self._current.get(session_id)
        if cur_id is None:
            return None
        return next((s for s in self._stages if s.id == cur_id), None)

    def next_after(self, session_id: str) -> CaseStage | None:
        """Return the next incomplete CaseStage in sequence, or None if all done."""
        if not self._stages:
            return None
        completed = self._completed.get(session_id, set())
        by_id: dict[str, CaseStage] = {s.id: s for s in self._stages}
        cur: CaseStage | None = self._stages[0]
        seen: set[str] = set()
        while cur is not None:
            if cur.id in seen:
                break
            seen.add(cur.id)
            if cur.id not in completed:
                return cur
            if cur.on_complete == "end":
                break
            cur = by_id.get(cur.on_complete)
        return None

    def all_stages_completed(self, session_id: str) -> bool:
        """True if every stage in the sequence has a StageCompleted event."""
        if not self._stages:
            return True
        completed = self._completed.get(session_id, set())
        by_id: dict[str, CaseStage] = {s.id: s for s in self._stages}
        cur: CaseStage | None = self._stages[0]
        seen: set[str] = set()
        while cur is not None:
            if cur.id in seen:
                break
            seen.add(cur.id)
            if cur.id not in completed:
                return False
            if cur.on_complete == "end":
                break
            cur = by_id.get(cur.on_complete)
        return True
