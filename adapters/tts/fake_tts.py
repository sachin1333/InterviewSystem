from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from adapters.tts.contracts import TtsAllFailed, TtsTimeout

_SENT_RE = re.compile(r"(?<=[.!?])\s+")


class FakeTts:
    """Deterministic TTS for tests.

    fail_mode:
      None         - normal
      "first_byte" - raise TtsTimeout before any chunk
      "midstream"  - yield first chunk then raise RuntimeError
      "all_fail"   - raise TtsAllFailed (used by tests of the fallback chain)
    """

    def __init__(self, *, bytes_per_char: int = 2, fail_mode: str | None = None) -> None:
        self.bytes_per_char = bytes_per_char
        self.fail_mode = fail_mode

    async def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice_id: str,
    ) -> AsyncIterator[bytes]:
        text_parts = []
        async for c in text_chunks:
            text_parts.append(c)
        full = "".join(text_parts).strip()
        if not full:
            return
        if self.fail_mode == "first_byte":
            raise TtsTimeout("simulated first-byte timeout")
        if self.fail_mode == "all_fail":
            raise TtsAllFailed("simulated all providers down")
        sentences = [s for s in _SENT_RE.split(full) if s]
        for i, s in enumerate(sentences):
            await asyncio.sleep(0)
            if self.fail_mode == "midstream" and i > 0:
                raise RuntimeError("simulated mid-stream failure")
            yield b"\x00" * (len(s) * self.bytes_per_char)
