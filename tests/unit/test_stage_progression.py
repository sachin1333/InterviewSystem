from pathlib import Path

from core.case_loader import load_case
from core.events import StageCompleted, StageEntered
from core.projections import StageStore


def _store() -> StageStore:
    case = load_case(Path("templates/cases/multi_stage_case_v1.yaml"))
    return StageStore(case.stages)


def test_stage_store_tracks_current_stage() -> None:
    store = _store()
    store.apply_for_session("sess", StageEntered(stage_id="problem_framing", primitive="think_aloud"))
    cur = store.current("sess")
    assert cur is not None and cur.id == "problem_framing"

    store.apply_for_session("sess", StageEntered(stage_id="methodology", primitive="think_aloud"))
    cur2 = store.current("sess")
    assert cur2 is not None and cur2.id == "methodology"


def test_stage_store_advances_after_completion() -> None:
    case = load_case(Path("templates/cases/multi_stage_case_v1.yaml"))
    store = StageStore(case.stages)
    seq = case.stage_sequence()

    store.apply_for_session("sess", StageEntered(stage_id=seq[0].id, primitive=seq[0].primitive.value))
    store.apply_for_session("sess", StageCompleted(stage_id=seq[0].id))
    nxt = store.next_after("sess")
    assert nxt is not None and nxt.id == seq[1].id


def test_stage_store_all_stages_completed() -> None:
    case = load_case(Path("templates/cases/multi_stage_case_v1.yaml"))
    store = StageStore(case.stages)
    seq = case.stage_sequence()

    assert not store.all_stages_completed("sess")
    for stage in seq:
        store.apply_for_session("sess", StageEntered(stage_id=stage.id, primitive=stage.primitive.value))
        store.apply_for_session("sess", StageCompleted(stage_id=stage.id))

    assert store.all_stages_completed("sess")
    assert store.next_after("sess") is None
