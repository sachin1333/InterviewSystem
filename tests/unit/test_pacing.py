from __future__ import annotations

from core.pacing import Pacer


def test_pacer_backchannels_once_per_candidate_turn() -> None:
    p = Pacer(backchannels=("got it",))
    assert p.backchannel_for("turn-1", already_emitted=False) == "got it"
    assert p.backchannel_for("turn-1", already_emitted=True) is None


def test_pacer_break_due_after_answer_threshold() -> None:
    p = Pacer(break_after_answers=3)
    assert not p.should_offer_break(answer_count=2, break_already_offered=False)
    assert p.should_offer_break(answer_count=3, break_already_offered=False)
    assert not p.should_offer_break(answer_count=4, break_already_offered=True)


def test_pacer_floor_sleep_uses_injected_sleep() -> None:
    slept: list[float] = []
    p = Pacer(response_floor_ms=800, sleep=slept.append)
    slept_ms = p.apply_floor(started_monotonic=10.0, now_monotonic=lambda: 10.2)
    assert 590 <= slept_ms <= 610
    assert slept == [slept_ms / 1000]
