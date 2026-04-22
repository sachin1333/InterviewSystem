from datetime import datetime

from core.domain import Actor, ArtifactKind, TurnKind
from core.eventlog import InMemoryEventLog
from core.events import ArtifactAttached, Envelope, SessionStarted, TurnPosted
from core.orchestrator import RequestScore, next_action
from core.projections import ScoreStore, SessionStore


def test_golden_sequence_requests_score():
    log = InMemoryEventLog()
    # golden sequence: session started, a turn posted, artifact attached
    e1 = Envelope(session_id="golden", seq=1, at=datetime(2026,4,22,10,0,0), payload=SessionStarted(rubric_version="ds_mle@1"))
    e2 = Envelope(session_id="golden", seq=2, at=datetime(2026,4,22,10,0,1), payload=TurnPosted(id="t1", actor=Actor.candidate, kind=TurnKind.answer))
    e3 = Envelope(session_id="golden", seq=3, at=datetime(2026,4,22,10,0,2), payload=ArtifactAttached(id="a1", kind=ArtifactKind.markdown, produced_by_turn_id="t1", version=1))
    log.append(e1)
    log.append(e2)
    log.append(e3)

    # replay into projections
    sessions = SessionStore()
    scores = ScoreStore()
    for ev in log.get_session("golden"):
        sessions.apply(ev)
        scores.apply(ev)

    action = next_action("golden", sessions, scores)
    assert isinstance(action, RequestScore)
