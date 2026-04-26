from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from adapters.stt.contracts import SttConnectionError, SttPartial


class FakeStt:
    """Deterministic STT for unit + chaos tests.

    Constructor flags:
      script: scripted partials/finals to emit
      fail_mode: None | "connect" | "midstream" | "no_partials"
        - "connect": raise SttConnectionError before yielding anything
        - "midstream": yield half the script, then raise SttConnectionError
        - "no_partials": consume audio without yielding anything
    """

    def __init__(
        self,
        *,
        script: list[SttPartial] | None = None,
        fail_mode: str | None = None,
    ) -> None:
        self.script = list(script or [])
        self.fail_mode = fail_mode

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        sample_rate_hz: int = 16000,
    ) -> AsyncIterator[SttPartial]:
        if self.fail_mode == "connect":
            raise SttConnectionError("simulated connection failure")
        # Drain audio in the background so callers don't deadlock.
        async def _drain() -> None:
            async for _ in audio_chunks:
                pass
        drain_task = asyncio.create_task(_drain())
        try:
            half = max(1, len(self.script) // 2)
            for i, partial in enumerate(self.script):
                await asyncio.sleep(0)
                if self.fail_mode == "midstream" and i >= half:
                    raise SttConnectionError("simulated mid-stream disconnect")
                if self.fail_mode == "no_partials":
                    continue
                yield partial
        finally:
            drain_task.cancel()
            with contextlib.suppress(BaseException):
                await drain_task
