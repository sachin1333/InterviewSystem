"""FastAPI HTTP surface for the interview system.

Minimal: ugly HTML forms, no JS framework, no external CSS.
Candidate flow:
  1. POST /sessions                     → create session, redirect to GET /sessions/{id}
  2. GET  /sessions/{id}                → advance FSM, render question/probe form
  3. POST /sessions/{id}/turn           → submit answer + optional code, redirect to 2
  4. GET  /sessions/{id}/result         → render feedback markdown
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.session_runner import SessionRunner
from core.contracts import EventLog
from core.domain import Actor, ArtifactKind, TurnKind
from core.events import ArtifactAttached, CandidateJoined, Envelope, SessionStarted, TurnPosted
from core.pack_loader import PackRegistry
from core.session_boot import replay

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _conversation_messages(session_id: str, log: EventLog) -> list[dict[str, str]]:
    """Build an ordered list of {role, text} messages for the chat view."""
    sessions, _, _, _, artifacts = replay(session_id, log)
    s = sessions.get(session_id)
    if s is None:
        return []
    out: list[dict[str, str]] = []
    for turn in s["turns"]:
        role = "interviewer" if turn["actor"] in (Actor.challenger, Actor.examiner) else "candidate"
        # Concatenate all artifacts produced by this turn (usually one).
        chunks: list[str] = []
        for aid, tid in s["artifact_turn"].items():
            if tid != turn["id"]:
                continue
            body = artifacts.get(aid)
            if body:
                chunks.append(body)
        if not chunks:
            continue
        out.append({"role": role, "text": "\n\n".join(chunks)})
    return out


def make_app(
    *,
    log: EventLog,
    runner: SessionRunner,
    target_answers: int = 1,
    max_probes: int = 0,
    rubric_version: str = "ds-ml-engineer@1",
    output_dir: Path | str | None = None,
    pack_registry: PackRegistry | None = None,
) -> FastAPI:
    """Return a configured FastAPI application.

    Parameters are injected rather than read from globals so the same factory
    can be called in tests with fake adapters.
    """
    app = FastAPI(title="Interview System")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Store config on app.state so route handlers can read it.
    app.state.log = log
    app.state.runner = runner
    app.state.target_answers = target_answers
    app.state.max_probes = max_probes
    app.state.rubric_version = rubric_version
    app.state.pack_registry = pack_registry or PackRegistry()

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
        return templates.TemplateResponse(request, "turn.html", {
            "session_id": session_id,
            "messages": messages,
            "turn_kind": turn_kind,
            "turn_nonce": uuid.uuid4().hex,
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
    from adapters.llm.factory import get_model_router
    from adapters.scorer.aggregator import RubricAggregator
    from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
    from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
    from core.domain import Dimension

    log = SqliteEventLog(db_path)
    router = get_model_router()
    rubric_path = _Path("templates") / "rubrics" / "ds-ml-engineer-v1.yaml"
    output_dir = _Path(os.getenv("OUTPUT_DIR", "outputs"))
    aggregator = RubricAggregator.from_yaml(rubric_path, output_dir=output_dir)

    runner = SessionRunner(
        challenger=LlmChallenger(router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.communication: LlmCommunicationScorer(router),
        },
        aggregator=aggregator,
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )
    return make_app(log=log, runner=runner, output_dir=output_dir)
