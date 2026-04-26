from __future__ import annotations

import os

from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.stt.wispr_flow import WisprFlowStt

_CANNED_SCRIPT = [
    SttPartial(text="I'd start by", is_final=False, elapsed_ms=120),
    SttPartial(text="I'd start by clarifying the goal", is_final=False, elapsed_ms=320),
    SttPartial(text="I'd start by clarifying the goal and the metric.", is_final=True, elapsed_ms=850),
]


def make_stt() -> FakeStt | WisprFlowStt:
    if os.environ.get("WISPR_API_KEY"):
        return WisprFlowStt()
    return FakeStt(script=list(_CANNED_SCRIPT))
