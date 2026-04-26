from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SttPartial:
    text: str
    is_final: bool
    elapsed_ms: int


class SttTimeout(RuntimeError):
    """Raised when no STT partial is received within the configured window."""


class SttConnectionError(RuntimeError):
    """Raised when STT WebSocket cannot be established or is severed."""


class Stt(Protocol):
    def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        sample_rate_hz: int = 16000,
    ) -> AsyncIterator[SttPartial]: ...
