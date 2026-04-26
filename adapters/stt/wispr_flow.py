from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import AsyncIterator

from adapters.stt.contracts import SttConnectionError, SttPartial, SttTimeout


class WisprFlowStt:
    """Streaming STT via Wispr Flow Dash WebSocket.

    Endpoint: wss://platform-api.wisprflow.ai/api/v1/dash/ws?api_key=Bearer <API_KEY>
    Imports `websockets` lazily so test environments without it can still load FakeStt.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        first_partial_timeout_s: float = 3.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("WISPR_API_KEY", "")
        self.first_partial_timeout_s = first_partial_timeout_s

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        sample_rate_hz: int = 16000,
    ) -> AsyncIterator[SttPartial]:
        if not self.api_key:
            raise SttConnectionError("WISPR_API_KEY not set")
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise SttConnectionError("websockets package required for WisprFlowStt") from exc

        url = f"wss://platform-api.wisprflow.ai/api/v1/dash/ws?api_key=Bearer%20{self.api_key}"
        started = time.monotonic()
        try:
            async with websockets.connect(url) as ws:
                # Forward audio in the background.
                async def _forward() -> None:
                    async for chunk in audio_chunks:
                        await ws.send(json.dumps({
                            "audio": base64.b64encode(chunk).decode("ascii"),
                            "sample_rate_hz": sample_rate_hz,
                        }))
                    await ws.send(json.dumps({"event": "audio_end"}))

                forward_task = asyncio.create_task(_forward())
                first_partial_seen = False
                try:
                    while True:
                        try:
                            timeout = self.first_partial_timeout_s if not first_partial_seen else 30.0
                            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        except TimeoutError as exc:
                            if not first_partial_seen:
                                raise SttTimeout("no partial within window") from exc
                            break
                        msg = json.loads(raw)
                        text = msg.get("text", "")
                        is_final = bool(msg.get("is_final", False))
                        elapsed_ms = int((time.monotonic() - started) * 1000)
                        first_partial_seen = True
                        yield SttPartial(text=text, is_final=is_final, elapsed_ms=elapsed_ms)
                        if is_final:
                            break
                finally:
                    forward_task.cancel()
        except OSError as exc:
            raise SttConnectionError(f"wispr connect failed: {exc}") from exc
