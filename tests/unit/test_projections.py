from datetime import UTC, datetime

from core.domain import Actor, ArtifactKind, Dimension, Score, TurnKind
from core.events import ArtifactAttached, Envelope, ScoreComputed, SessionStarted, TurnPosted
from core.projections import ScoreStore, SessionStore

UTC = UTC


def test_session_store_tracks_start_turns_artifacts():
    store = SessionStore()
    e1 = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC), payload=SessionStarted(rubric_version="v1"))
    store.apply(e1)
    s = store.get("s1")
    assert s["rubric_version"] == "v1"
    assert s["turn_ids"] == ()

    e2 = Envelope(session_id="s1", seq=2, at=datetime(2026,4,22,10,0,1,tzinfo=UTC), payload=TurnPosted(id="t1", actor=Actor.candidate, kind=TurnKind.answer))
    store.apply(e2)
    assert store.get("s1")["turn_ids"] == ("t1",)

    e3 = Envelope(session_id="s1", seq=3, at=datetime(2026,4,22,10,0,2,tzinfo=UTC), payload=ArtifactAttached(id="a1", kind=ArtifactKind.markdown, produced_by_turn_id="t1", version=1))
    store.apply(e3)
    assert store.get("s1")["artifact_ids"] == ("a1",)


def test_score_store_records_latest():
    ss = ScoreStore()
    sc = Score(session_id="s2", rubric_version="v1", per_dimension={Dimension.communication: 0.7}, composite=0.7, at=datetime(2026,4,22,10,0,5,tzinfo=UTC))
    e = Envelope(session_id="s2", seq=10, at=datetime(2026,4,22,10,0,6,tzinfo=UTC), payload=ScoreComputed(score=sc))
    ss.apply(e)
    got = ss.get("s2")
    assert got == sc
