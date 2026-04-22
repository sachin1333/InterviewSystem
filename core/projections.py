from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from core.domain import Score
from core.events import ArtifactAttached, Envelope, ScoreComputed, SessionStarted, TurnPosted


class _SessionRecord(TypedDict):
    rubric_version: str | None
    started_at: datetime | None
    turn_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRecord] = {}

    def apply(self, envelope: Envelope) -> None:
        sid = envelope.session_id
        payload = envelope.payload
        if isinstance(payload, SessionStarted):
            self._sessions.setdefault(sid, {
                "rubric_version": payload.rubric_version,
                "started_at": envelope.at,
                "turn_ids": (),
                "artifact_ids": (),
            })
        elif isinstance(payload, TurnPosted):
            sess = self._sessions.setdefault(sid, {
                "rubric_version": None,
                "started_at": None,
                "turn_ids": (),
                "artifact_ids": (),
            })
            sess["turn_ids"] = sess["turn_ids"] + (payload.id,)
        elif isinstance(payload, ArtifactAttached):
            sess = self._sessions.setdefault(sid, {
                "rubric_version": None,
                "started_at": None,
                "turn_ids": (),
                "artifact_ids": (),
            })
            sess["artifact_ids"] = sess["artifact_ids"] + (payload.id,)

    def get(self, session_id: str) -> _SessionRecord | None:
        return self._sessions.get(session_id)


class ScoreStore:
    def __init__(self) -> None:
        self._scores: dict[str, Score] = {}

    def apply(self, envelope: Envelope) -> None:
        sid = envelope.session_id
        payload = envelope.payload
        if isinstance(payload, ScoreComputed):
            self._scores[sid] = payload.score

    def get(self, session_id: str) -> Score | None:
        return self._scores.get(session_id)
