"""FastAPI HTTP surface for the interview system.

Minimal: ugly HTML forms, no JS framework, no external CSS.
Candidate flow:
  1. POST /sessions                     → create session, redirect to GET /sessions/{id}
  2. GET  /sessions/{id}                → advance FSM, render question/probe form
  3. POST /sessions/{id}/turn           → submit answer + optional code, redirect to 2
  4. GET  /sessions/{id}/result         → render feedback markdown
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.session_runner import SessionRunner
from adapters.http.voice_protocol import (
    ClientAudioChunk,
    ClientModeSwitch,
    ServerPartialTranscript,
    ServerStageChange,
    ServerTtsChunk,
)
from core.contracts import EventLog
from core.domain import Actor, ArtifactKind, TurnKind
from core.events import (
    ArtifactAttached,
    CandidateJoined,
    Envelope,
    ProblemIntroduced,
    SessionStarted,
    TurnPosted,
    TurnTimingObserved,
)
from core.pack_loader import PackRegistry
from core.primitives import Primitive
from core.session_boot import replay

if TYPE_CHECKING:
    from adapters.http.voice_runner import VoiceRunner

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _last_interviewer_has_text(messages: list[dict[str, str]]) -> bool:
    """Return True if the most recent interviewer message contains non-empty text."""
    for m in reversed(messages):
        if m["role"] == "interviewer":
            return bool(m["text"].strip())
    return False


def _conversation_messages(session_id: str, log: EventLog) -> list[dict[str, str]]:
    """Build ordered chat rows, including problem-boundary dividers."""
    sessions, _, _, _, artifacts = replay(session_id, log)
    s = sessions.get(session_id)
    if s is None:
        return []

    turn_by_id = {turn["id"]: turn for turn in s["turns"]}
    artifacts_by_turn: dict[str, list[str]] = {}
    for aid, tid in s["artifact_turn"].items():
        body = artifacts.get(aid)
        if body:
            artifacts_by_turn.setdefault(tid, []).append(body)

    out: list[dict[str, str]] = []
    for env in log.get_session(session_id):
        payload = env.payload
        if isinstance(payload, ProblemIntroduced):
            out.append({
                "role": "boundary",
                "text": f"Problem {payload.ordinal}: {payload.opener_text}",
            })
        elif isinstance(payload, TurnPosted):
            chunks = artifacts_by_turn.get(payload.id, [])
            if not chunks:
                continue
            turn = turn_by_id.get(payload.id)
            actor = payload.actor if turn is None else turn["actor"]
            role = "interviewer" if actor in (Actor.challenger, Actor.examiner) else "candidate"
            out.append({"role": role, "text": "\n\n".join(chunks)})
    return out


def _problem_progress(session_id: str, log: EventLog, runner: SessionRunner) -> str | None:
    """Return candidate-facing problem progress like 'Problem 2 of 3'."""
    sessions, _, _, _, _ = replay(session_id, log)
    record = sessions.get(session_id)
    if record is None:
        return None

    current_id = record["current_problem_id"]
    last_ordinal: int | None = None
    current_ordinal: int | None = None
    for env in log.get_session(session_id):
        payload = env.payload
        if isinstance(payload, ProblemIntroduced):
            last_ordinal = payload.ordinal
            if current_id is not None and payload.problem_id == current_id:
                current_ordinal = payload.ordinal
    ordinal = current_ordinal or last_ordinal
    if ordinal is None:
        return None

    planned_total = len(runner.problems)
    if planned_total == 0 and runner.problem_bank is not None:
        planned_total = len(runner.problem_bank.pick_sequence(session_id))
    total = max(planned_total, ordinal)
    return f"Problem {ordinal} of {total}"


def _probe_stream_pending(session_id: str, log: EventLog) -> bool:
    """True when the latest examiner probe turn has no rendered text yet."""
    sessions, _, _, _, _ = replay(session_id, log)
    s = sessions.get(session_id)
    if s is None:
        return False
    for turn in reversed(s["turns"]):
        if turn["actor"] == Actor.candidate:
            return False
        if turn["actor"] == Actor.examiner and turn["kind"] == TurnKind.probe:
            return not any(tid == turn["id"] for tid in s["artifact_turn"].values())
    return False


def make_app(
    *,
    log: EventLog,
    runner: SessionRunner,
    target_answers: int = 1,
    max_probes: int = 0,
    rubric_version: str = "ds-ml-engineer@1",
    output_dir: Path | str | None = None,
    pack_registry: PackRegistry | None = None,
    voice_runner: VoiceRunner | None = None,
    voice_mode: str = "off",
    voice_latency_budget_ms: int = 800,
) -> FastAPI:
    """Return a configured FastAPI application.

    Parameters are injected rather than read from globals so the same factory
    can be called in tests with fake adapters.
    """
    app = FastAPI(title="Interview System")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Mount static files for voice.js and voice.css
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Store config on app.state so route handlers can read it.
    app.state.log = log
    app.state.runner = runner
    app.state.target_answers = target_answers
    app.state.max_probes = max_probes
    app.state.rubric_version = rubric_version
    app.state.pack_registry = pack_registry or PackRegistry()
    app.state.voice_runner = voice_runner
    app.state.voice_mode = voice_mode
    app.state.voice_latency_budget_ms = voice_latency_budget_ms

    # ------------------------------------------------------------------ #
    #  Start page                                                          #
    # ------------------------------------------------------------------ #

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "start.html")

    # ------------------------------------------------------------------ #
    #  Create session                                                      #
    # ------------------------------------------------------------------ #

    @app.post("/sessions")
    async def create_session(
        candidate_handle: Annotated[str, Form()],
        pack_id: Annotated[str, Form()] = "ds-ml-v1",
    ) -> RedirectResponse:
        session_id = f"s-{uuid.uuid4().hex[:12]}"
        _log: EventLog = app.state.log
        now = datetime.now(UTC)

        _log.append(Envelope(
            session_id=session_id,
            seq=1,
            at=now,
            payload=SessionStarted(
                rubric_version=app.state.rubric_version,
                target_answers=app.state.target_answers,
                max_probes_per_prompt=app.state.max_probes,
                pack_id=pack_id,
            ),
        ))
        _log.append(Envelope(
            session_id=session_id,
            seq=2,
            at=now,
            payload=CandidateJoined(candidate_handle=candidate_handle.strip() or "candidate"),
        ))
        return RedirectResponse(f"/sessions/{session_id}", status_code=303)

    # ------------------------------------------------------------------ #
    #  Session turn view                                                   #
    # ------------------------------------------------------------------ #

    @app.get("/sessions/{session_id}", response_class=HTMLResponse, response_model=None)
    async def get_session(request: Request, session_id: str) -> HTMLResponse | RedirectResponse:
        _log: EventLog = app.state.log
        _runner: SessionRunner = app.state.runner

        result = _runner.advance(session_id, _log)

        if result.state == "ended":
            return RedirectResponse(f"/sessions/{session_id}/result", status_code=303)

        turn_kind = (result.turn_kind or TurnKind.answer).value
        messages = _conversation_messages(session_id, _log)
        probe_pending = (
            _probe_stream_pending(session_id, _log)
            or (result.turn_kind == TurnKind.defense and not _last_interviewer_has_text(messages))
        )
        problem_progress = _problem_progress(session_id, _log, _runner)

        # Determine active input mode from cookie (voice sessions only).
        voice_on = app.state.voice_mode != "off"
        if voice_on:
            raw_cookie = request.cookies.get("interview_mode", "voice")
            active_mode = raw_cookie if raw_cookie in ("text", "voice") else "voice"
        else:
            active_mode = "text"

        return templates.TemplateResponse(request, "turn.html", {
            "session_id": session_id,
            "messages": messages,
            "turn_kind": turn_kind,
            "turn_nonce": uuid.uuid4().hex,
            "voice_mode": voice_on,
            "voice_mode_setting": app.state.voice_mode,
            "allow_text_switch": app.state.voice_mode == "on",
            "active_mode": active_mode,
            "probe_pending": probe_pending,
            "problem_progress": problem_progress,
        })

    # ------------------------------------------------------------------ #
    #  Submit candidate turn                                               #
    # ------------------------------------------------------------------ #

    @app.post("/sessions/{session_id}/turn")
    async def post_turn(
        session_id: str,
        answer: Annotated[str, Form()] = "",
        code: Annotated[str, Form()] = "",
        turn_nonce: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        started = time.monotonic()
        _log: EventLog = app.state.log
        now = datetime.now(UTC)

        # Determine turn kind from current projection.
        proj_sessions, _, _, _, _ = replay(session_id, _log)
        s = proj_sessions.get(session_id)
        turns = s["turns"] if s else []
        last_system = next(
            (t for t in reversed(turns) if t["actor"] in (Actor.challenger, Actor.examiner)),
            None,
        )
        kind = (
            TurnKind.defense
            if last_system is not None and last_system["kind"] == TurnKind.probe
            else TurnKind.answer
        )
        context_assembled_ms = max(0, int((time.monotonic() - started) * 1000))

        nonce = turn_nonce or uuid.uuid4().hex
        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        seq = (_log.last_seq(session_id) or 0) + 1

        # Append candidate turn (idempotent via nonce).
        _log.append(
            Envelope(
                session_id=session_id,
                seq=seq,
                at=now,
                payload=TurnPosted(id=turn_id, actor=Actor.candidate, kind=kind),
                idem_key=nonce,
            ),
            nonce,
        )

        answer_text = answer.strip()
        code_text = code.strip()

        if answer_text:
            seq = (_log.last_seq(session_id) or 0) + 1
            _log.append(
                Envelope(
                    session_id=session_id,
                    seq=seq,
                    at=now,
                    payload=ArtifactAttached(
                        id=f"a-{uuid.uuid4().hex[:8]}",
                        kind=ArtifactKind.markdown,
                        produced_by_turn_id=turn_id,
                        version=1,
                        content=answer_text,
                    ),
                    idem_key=f"{nonce}-answer",
                ),
                f"{nonce}-answer",
            )

        if code_text:
            seq = (_log.last_seq(session_id) or 0) + 1
            _log.append(
                Envelope(
                    session_id=session_id,
                    seq=seq,
                    at=now,
                    payload=ArtifactAttached(
                        id=f"a-{uuid.uuid4().hex[:8]}",
                        kind=ArtifactKind.code_cell,
                        produced_by_turn_id=turn_id,
                        version=1,
                        content=code_text,
                    ),
                    idem_key=f"{nonce}-code",
                ),
                f"{nonce}-code",
            )

        first_paint_ms = max(0, int((time.monotonic() - started) * 1000))
        seq = (_log.last_seq(session_id) or 0) + 1
        _log.append(
            Envelope(
                session_id=session_id,
                seq=seq,
                at=now,
                payload=TurnTimingObserved(
                    turn_id=turn_id,
                    phase="candidate_submit",
                    submit_received_ms=0,
                    context_assembled_ms=context_assembled_ms,
                    first_token_ms=context_assembled_ms,
                    first_paint_ms=first_paint_ms,
                ),
                idem_key=f"{nonce}-timing",
            ),
            f"{nonce}-timing",
        )

        return RedirectResponse(f"/sessions/{session_id}", status_code=303)

    # ------------------------------------------------------------------ #
    #  Result page                                                         #
    # ------------------------------------------------------------------ #

    @app.get("/sessions/{session_id}/result", response_class=HTMLResponse)
    async def get_result(request: Request, session_id: str) -> HTMLResponse:
        _log: EventLog = app.state.log
        _, scores, _, _, _ = replay(session_id, _log)
        score = scores.get(session_id)

        feedback_md = ""
        if output_dir is not None:
            feedback_path = Path(output_dir) / f"{session_id}_feedback.md"
        else:
            feedback_path = Path("outputs") / f"{session_id}_feedback.md"
        if feedback_path.exists():
            feedback_md = feedback_path.read_text(encoding="utf8")

        return templates.TemplateResponse(request, "result.html", {
            "session_id": session_id,
            "score": score,
            "feedback_md": feedback_md,
        })

    # ------------------------------------------------------------------ #
    #  WebSocket voice interview                                          #
    # ------------------------------------------------------------------ #

    @app.websocket("/ws/sessions/{session_id}/voice")
    async def voice_websocket(websocket: WebSocket, session_id: str) -> None:
        """Bidirectional voice interview over WebSocket.

        Protocol:
          1. Client connects with session_id
          2. Client sends audio_chunk messages with PCM audio
          3. Server streams back partial_transcript, tts_chunk, stage_change
          4. Client can send mode_switch to escape to text mode (closes WS)

        Auth: For now, we accept any session_id (real auth deferred).
        """
        voice_runner: VoiceRunner | None = app.state.voice_runner

        if voice_runner is None:
            await websocket.close(code=1011, reason="voice not configured")
            return

        _log: EventLog = app.state.log

        # Check that session exists (last_seq != None).
        if _log.last_seq(session_id) is None:
            await websocket.close(code=1008, reason="session not found")
            return

        await websocket.accept()

        # Queue to bridge sync WS recv loop → async iterator.
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def _audio_iterator() -> AsyncIterator[bytes]:
            """Async iterator over queued audio bytes. Stops at None sentinel."""
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                yield chunk

        # Background task to read WS messages and feed the queue.
        async def _read_messages() -> None:
            try:
                while True:
                    data = await websocket.receive_json()
                    msg_type = data.get("type")

                    if msg_type == "audio_chunk":
                        if not isinstance(data, dict):
                            continue
                        audio_msg = cast(ClientAudioChunk, data)
                        if audio_msg["seq"] == -1:
                            # End-of-stream sentinel
                            await audio_queue.put(None)
                            break
                        else:
                            # Decode base64 PCM
                            pcm_bytes = base64.b64decode(audio_msg["pcm_b64"])
                            await audio_queue.put(pcm_bytes)

                    elif msg_type == "mode_switch":
                        if not isinstance(data, dict):
                            continue
                        mode_msg = cast(ClientModeSwitch, data)
                        if mode_msg["mode"] == "text":
                            # Close the voice channel gracefully.
                            await audio_queue.put(None)
                            await websocket.close(code=1000, reason="switched to text mode")
                            return
            except WebSocketDisconnect:
                # Client disconnected. Signal audio iterator to stop.
                await audio_queue.put(None)
            except Exception:
                # On any error, close the queue and let next_turn handle it.
                await audio_queue.put(None)

        # Start reading messages in background.
        read_task = asyncio.create_task(_read_messages())

        try:
            # Run the voice turn.
            result = await voice_runner.next_turn(
                session_id,
                candidate_audio=_audio_iterator(),
                sample_rate_hz=16000,
            )

            # Send transcript first.
            transcript_msg: ServerPartialTranscript = {
                "type": "partial_transcript",
                "text": result.transcript,
                "is_final": True,
            }
            await websocket.send_json(transcript_msg)

            # Stream TTS audio chunks.
            first_chunk = True
            async for audio_chunk in result.audio_stream:
                tts_msg: ServerTtsChunk = {
                    "type": "tts_chunk",
                    "pcm_b64": base64.b64encode(audio_chunk).decode(),
                    "end_of_utterance": False,
                }
                await websocket.send_json(tts_msg)
                first_chunk = False

            # Send final tts_chunk marker.
            if not first_chunk:
                tts_msg = {
                    "type": "tts_chunk",
                    "pcm_b64": "",
                    "end_of_utterance": True,
                }
                await websocket.send_json(tts_msg)

            # Determine whether the next stage benefits from the text panel.
            next_stage = result.case_stage
            next_case = voice_runner.case
            next_stage_obj = next((s for s in next_case.stages if s.id == next_stage), None)
            if next_stage_obj:
                show_text_panel = _primitive_needs_text_panel(next_stage_obj.primitive)
            else:
                show_text_panel = True

            # Send stage change.
            stage_msg: ServerStageChange = {
                "type": "stage_change",
                "case_stage": result.case_stage,
                "primitive": next_stage_obj.primitive.value if next_stage_obj else "",
                "show_text_panel": show_text_panel,
            }
            await websocket.send_json(stage_msg)

            # WS closes normally.
            await websocket.close(code=1000)

        except Exception as e:
            # Log error and close.
            print(f"Error in voice_websocket: {e}")
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="server error")
        finally:
            # Ensure read task is cancelled.
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read_task

    # ------------------------------------------------------------------ #
    #  SSE streaming probe endpoint                                       #
    # ------------------------------------------------------------------ #

    if runner.examiner is not None:
        from adapters.http.sse_routes import make_sse_router
        app.include_router(make_sse_router(log=log, examiner=runner.examiner))

    return app


def create_app(db_path: str = "interview.db") -> FastAPI:
    """Production entry point.  Reads env for adapter config.

    Automatically loads a .env file from the project root if present,
    so credentials can be kept out of the shell environment.
    """
    import os
    from pathlib import Path as _Path

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_Path(__file__).parent.parent.parent / ".env", override=False)

    from adapters.challenger.llm_challenger import LlmChallenger
    from adapters.http.voice_runner import VoiceRunner
    from adapters.llm.factory import get_model_router
    from adapters.scorer.aggregator import RubricAggregator
    from adapters.scorer.authenticity_scorer import AuthenticityScorer
    from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
    from adapters.scorer.llm_insight_interp_scorer import LlmInsightInterpScorer
    from adapters.scorer.llm_problem_framing_scorer import LlmProblemFramingScorer
    from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
    from adapters.stt.factory import make_stt
    from adapters.tts.cartesia_sonic import CartesiaSonicTts
    from adapters.tts.elevenlabs_flash import ElevenLabsFlashTts
    from adapters.tts.fake_tts import FakeTts
    from adapters.tts.fallback_chain import FallbackChainTts
    from core.case_bank import CaseBank
    from core.domain import Dimension
    from core.problem_bank import ProblemBank

    log = SqliteEventLog(db_path)
    router = get_model_router()
    voice_mode = os.getenv("VOICE_MODE", "off").strip().lower() or "off"
    if voice_mode not in {"off", "on", "forced"}:
        voice_mode = "off"
    voice_latency_budget_ms = int(os.getenv("VOICE_LATENCY_BUDGET_MS", "800"))
    rubric_path = _Path("templates") / "rubrics" / "ds-ml-engineer-v1.yaml"
    output_dir = _Path(os.getenv("OUTPUT_DIR", "outputs"))
    aggregator = RubricAggregator.from_yaml(rubric_path, output_dir=output_dir)
    communication_scorer = LlmCommunicationScorer(router)

    case_bank_path = _Path("templates") / "cases" / "cold_start_bank.yaml"
    case_bank = CaseBank.from_yaml(case_bank_path) if case_bank_path.exists() else None
    problem_bank_path = _Path("templates") / "problem_banks" / "ds-ml-engineer-v1.yaml"
    problem_bank = ProblemBank.from_yaml(problem_bank_path) if problem_bank_path.exists() else None
    if problem_bank is not None:
        problem_bank.prewarm_openers()

    runner = SessionRunner(
        challenger=LlmChallenger(router, case_bank=case_bank),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.communication: communication_scorer,
            Dimension.problem_framing: LlmProblemFramingScorer(router),
            Dimension.insight_interp: LlmInsightInterpScorer(router),
        },
        aggregator=aggregator,
        problem_bank=problem_bank,
        scored_dimensions=(
            Dimension.problem_framing,
            Dimension.model_rationale,
            Dimension.insight_interp,
            Dimension.communication,
        ),
    )
    voice_runner = None
    if voice_mode != "off":
        primary_tts = CartesiaSonicTts()
        fallback_tts = ElevenLabsFlashTts()
        tts = (
            FallbackChainTts(primary_tts, fallback_tts)
            if (os.getenv("CARTESIA_API_KEY") or os.getenv("ELEVENLABS_API_KEY"))
            else FakeTts(bytes_per_char=2)
        )
        voice_runner = VoiceRunner(
            log=log,
            router=router,
            stt=make_stt(),
            tts=tts,
            communication_scorer=communication_scorer,
            authenticity_scorer=AuthenticityScorer(),
        )
    return make_app(
        log=log,
        runner=runner,
        output_dir=output_dir,
        voice_runner=voice_runner,
        voice_mode=voice_mode,
        voice_latency_budget_ms=voice_latency_budget_ms,
    )


def _primitive_needs_text_panel(primitive: Primitive) -> bool:
    return primitive in {Primitive.verbal_whiteboard}
