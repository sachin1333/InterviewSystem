from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from core.projections import ScoreStore, SessionStore


class Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NoAction(Action):
    reason: str = "no-op"


class RequestScore(Action):
    session_id: str


class EndSession(Action):
    session_id: str
    reason: str


ActionT = NoAction | RequestScore | EndSession


def next_action(session_id: str, sessions: SessionStore, scores: ScoreStore) -> ActionT:
    s = sessions.get(session_id)
    if s is None:
        return NoAction(reason="unknown-session")
    # request scoring when there are artifacts and no score yet
    if (s.get("artifact_ids") or ()) and scores.get(session_id) is None:
        return RequestScore(session_id=session_id)
    return NoAction(reason="nothing-to-do")
