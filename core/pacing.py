from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Pacer:
    response_floor_ms: int = 800
    break_after_answers: int = 6
    backchannels: Sequence[str] = ("got it", "okay", "makes sense")
    sleep: Callable[[float], None] = time.sleep

    def backchannel_for(self, candidate_turn_id: str, *, already_emitted: bool) -> str | None:
        if already_emitted or not candidate_turn_id or candidate_turn_id == "none":
            return None
        return self.backchannels[hash(candidate_turn_id) % len(self.backchannels)]

    def should_offer_break(self, *, answer_count: int, break_already_offered: bool) -> bool:
        return not break_already_offered and answer_count >= self.break_after_answers

    def apply_floor(
        self,
        *,
        started_monotonic: float,
        now_monotonic: Callable[[], float] = time.monotonic,
    ) -> int:
        elapsed_ms = max(0, int((now_monotonic() - started_monotonic) * 1000))
        remaining_ms = self.response_floor_ms - elapsed_ms
        if remaining_ms <= 0:
            return 0
        self.sleep(remaining_ms / 1000)
        return remaining_ms
