from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from urllib import request as urllib_request
from urllib.error import URLError

from adapters.tts.contracts import TtsAllFailed, TtsTimeout


class ElevenLabsFlashTts:
    """Streaming TTS via ElevenLabs Flash v2.5.

    First-byte target ≤120ms.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str = "eleven_flash_v2_5",
        first_byte_timeout_s: float = 0.30,
    ) -> None:
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.model_id = model_id
        self.first_byte_timeout_s = first_byte_timeout_s

    async def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice_id: str,
    ) -> AsyncIterator[bytes]:
        if not self.api_key:
            raise TtsAllFailed("ELEVENLABS_API_KEY not set")
        text_parts: list[str] = []
        async for c in text_chunks:
            text_parts.append(c)
        full = "".join(text_parts).strip()
        if not full:
            return
        payload = json.dumps({
            "text": full,
            "model_id": self.model_id,
            "output_format": "pcm_16000",
        }).encode()
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000"
        req = urllib_request.Request(
            url,
            data=payload,
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        def _open():
            try:
                return urllib_request.urlopen(req, timeout=self.first_byte_timeout_s + 5.0)
            except TimeoutError as exc:
                raise TtsTimeout(f"elevenlabs timeout: {exc}") from exc
            except URLError as exc:
                raise TtsAllFailed(f"elevenlabs connect: {exc}") from exc

        resp = await asyncio.to_thread(_open)
        try:
            while True:
                chunk = await asyncio.to_thread(resp.read, 4096)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()
