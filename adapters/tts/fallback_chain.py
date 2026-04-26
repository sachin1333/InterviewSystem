from __future__ import annotations

from collections.abc import AsyncIterator

from adapters.tts.contracts import Tts, TtsAllFailed, TtsTimeout


class FallbackChainTts:
    """Primary TTS with fallback on first-byte failure.

    Materializes incoming text_chunks once and feeds each provider its own iterator,
    so the fallback can run after primary failure without losing any input.
    """

    def __init__(self, primary: Tts, fallback: Tts) -> None:
        self.primary = primary
        self.fallback = fallback
        self._events: list[str] = []

    async def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice_id: str,
    ) -> AsyncIterator[bytes]:
        # Materialize once.
        buffered: list[str] = []
        async for c in text_chunks:
            buffered.append(c)

        async def _iter(lst: list[str]) -> AsyncIterator[str]:
            for item in lst:
                yield item

        try:
            async for chunk in self.primary.synthesize(_iter(buffered), voice_id=voice_id):
                yield chunk
            return
        except (TtsTimeout, TtsAllFailed):
            pass
        # Try fallback.
        self._events.append("tier_fallback")
        try:
            async for chunk in self.fallback.synthesize(_iter(buffered), voice_id=voice_id):
                yield chunk
        except (TtsTimeout, TtsAllFailed) as exc:
            raise TtsAllFailed("primary and fallback TTS providers both failed") from exc

    def consume_events(self) -> tuple[str, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events
