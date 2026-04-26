from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from urllib import request as urllib_request
from urllib.error import URLError

from adapters.tts.contracts import TtsAllFailed, TtsTimeout


class CartesiaSonicTts:
    """Streaming TTS via Cartesia Sonic.

    First-byte target ≤100ms. Falls back to TtsAllFailed if API key absent.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str = "sonic-english",
        first_byte_timeout_s: float = 0.25,
    ) -> None:
        self.api_key = api_key or os.environ.get("CARTESIA_API_KEY", "")
        self.model_id = model_id
        self.first_byte_timeout_s = first_byte_timeout_s

    async def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice_id: str,
    ) -> AsyncIterator[bytes]:
        if not self.api_key:
            raise TtsAllFailed("CARTESIA_API_KEY not set")
        text_parts: list[str] = []
        async for c in text_chunks:
            text_parts.append(c)
        full = "".join(text_parts).strip()
        if not full:
            return
        payload = json.dumps({
            "model_id": self.model_id,
            "voice": {"mode": "id", "id": voice_id},
            "transcript": full,
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 16000},
        }).encode()
        req = urllib_request.Request(
            "https://api.cartesia.ai/tts/sse",
            data=payload,
            headers={
                "X-API-Key": self.api_key,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        def _open():
            try:
                return urllib_request.urlopen(req, timeout=self.first_byte_timeout_s + 5.0)
            except TimeoutError as exc:
                raise TtsTimeout(f"cartesia timeout: {exc}") from exc
            except URLError as exc:
                raise TtsAllFailed(f"cartesia connect: {exc}") from exc

        resp = await asyncio.to_thread(_open)
        try:
            while True:
                chunk = await asyncio.to_thread(resp.read, 4096)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()
