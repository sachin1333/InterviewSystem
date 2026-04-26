from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class TtsAllFailed(RuntimeError):
    """Raised when every provider in the chain failed to produce audio."""


class TtsTimeout(RuntimeError):
    """Raised when first-byte was not received within the SLA."""


class Tts(Protocol):
    def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice_id: str,
    ) -> AsyncIterator[bytes]: ...
