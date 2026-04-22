from datetime import UTC, datetime

from core.domain import ArtifactKind
from core.events import ArtifactAttached, Envelope, SessionStarted
from core.orchestrator import NoAction, RequestScore, next_action
from core.projections import ScoreStore, SessionStore

UTC = UTC


def test_unknown_session_no_action():
    sessions = SessionStore()
    scores = ScoreStore()
    a = next_action("missing", sessions, scores)
    assert isinstance(a, NoAction)


def test_request_score_when_artifacts_and_no_score():
    sessions = SessionStore()
    scores = ScoreStore()
    e1 = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC), payload=SessionStarted(rubric_version="v1"))
    sessions.apply(e1)
    e2 = Envelope(session_id="s1", seq=2, at=datetime(2026,4,22,10,0,1,tzinfo=UTC), payload=ArtifactAttached(id="a1", kind=ArtifactKind.markdown, produced_by_turn_id="t1", version=1))
    sessions.apply(e2)
    a = next_action("s1", sessions, scores)
    assert isinstance(a, RequestScore) and a.session_id == "s1"


def test_no_action_if_score_exists():
    sessions = SessionStore()
    scores = ScoreStore()
    e1 = Envelope(session_id="s2", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC), payload=SessionStarted(rubric_version="v1"))
    sessions.apply(e1)
    e2 = Envelope(session_id="s2", seq=2, at=datetime(2026,4,22,10,0,1,tzinfo=UTC), payload=ArtifactAttached(id="a1", kind=ArtifactKind.markdown, produced_by_turn_id="t1", version=1))
    sessions.apply(e2)
    # craft a fake ScoreComputed envelope and apply to ScoreStore
    from core.domain import Dimension, Score
    sc = Score(session_id="s2", rubric_version="v1", per_dimension={Dimension.communication: 0.5}, composite=0.5, at=datetime(2026,4,22,10,0,2,tzinfo=UTC))
    from core.events import Envelope as Env
    from core.events import ScoreComputed
    e_sc = Env(session_id="s2", seq=3, at=datetime(2026,4,22,10,0,3,tzinfo=UTC), payload=ScoreComputed(score=sc))
    scores.apply(e_sc) if hasattr(scores, "apply") else scores._scores.__setitem__("s2", sc)
    a = next_action("s2", sessions, scores)
    assert isinstance(a, NoAction)
