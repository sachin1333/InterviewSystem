"""Server-Sent Events routes for persisted examiner probe text."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from adapters.examiner.llm_examiner import LlmExaminer
from core.contracts import EventLog
from core.domain import Actor, TurnKind
from core.session_boot import replay


def make_sse_router(*, log: EventLog, examiner: LlmExaminer) -> APIRouter:
    del examiner  # route streams persisted artifacts only; model calls happen in SessionRunner
    router = APIRouter()

    @router.get("/sessions/{session_id}/probe-stream")
    async def probe_stream(session_id: str, request: Request) -> StreamingResponse:
        del request  # unused — reserved for future auth / accept headers
        latest_text = _latest_persisted_probe_text(session_id, log)

        def _gen() -> Iterator[bytes]:
            if latest_text:
                yield f"data: {latest_text}\n\n".encode()
            yield b"event: done\ndata: [DONE]\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    return router


def _latest_persisted_probe_text(session_id: str, log: EventLog) -> str:
    sessions, _, _, _, artifacts = replay(session_id, log)
    s = sessions.get(session_id)
    if s is None:
        return ""

    for turn in reversed(s["turns"]):
        if turn["actor"] == Actor.examiner and turn["kind"] == TurnKind.probe:
            for artifact_id, turn_id in s["artifact_turn"].items():
                if turn_id == turn["id"]:
                    return artifacts.get(artifact_id) or ""
            return ""
    return ""
