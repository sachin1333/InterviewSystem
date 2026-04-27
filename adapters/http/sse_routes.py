"""Server-Sent Events routes for streaming examiner probes."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from adapters.examiner.llm_examiner import LlmExaminer
from core.contracts import EventLog
from core.session_boot import replay


def make_sse_router(*, log: EventLog, examiner: LlmExaminer) -> APIRouter:
    router = APIRouter()

    @router.get("/sessions/{session_id}/probe-stream")
    async def probe_stream(session_id: str, request: Request) -> StreamingResponse:
        del request  # unused — reserved for future auth / accept headers
        sessions, _, _, _, _ = replay(session_id, log)
        s = sessions.get(session_id)
        recent: list[object] = []
        if s is not None:
            recent = list(s["turns"][-6:])

        def _gen() -> Iterator[bytes]:
            for chunk in examiner.iter_review(session_id, recent):
                yield f"data: {chunk}\n\n".encode()
            yield b"event: done\ndata: [DONE]\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    return router
