from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Actor, ArtifactKind, Problem, ProblemId, TurnKind
from core.events import (
    ArtifactAttached,
    CandidateJoined,
    Envelope,
    ProblemClosed,
    ProblemIntroduced,
    SessionStarted,
    TurnPosted,
)


def _runner(tmp_path: Path) -> SessionRunner:
    router = ModelRouter(FakeRouter())
    aggregator = RubricAggregator.from_yaml(
        Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
        output_dir=tmp_path,
    )
    return SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=aggregator,
        problems=[
            Problem(id=ProblemId("p1"), opener_text="Problem one opener"),
            Problem(id=ProblemId("p2"), opener_text="Problem two opener"),
            Problem(id=ProblemId("p3"), opener_text="Problem three opener"),
        ],
    )


def _append(log: SqliteEventLog, session_id: str, payload: object) -> None:
    seq = (log.last_seq(session_id) or 0) + 1
    log.append(Envelope(session_id=session_id, seq=seq, at=datetime.now(UTC), payload=payload))


def _seed_two_problem_thread(log: SqliteEventLog, session_id: str) -> None:
    _append(log, session_id, SessionStarted(rubric_version="ds", target_answers=1, max_probes_per_prompt=1))
    _append(log, session_id, CandidateJoined(candidate_handle="alice"))
    _append(log, session_id, ProblemIntroduced(problem_id=ProblemId("p1"), opener_text="Problem one opener", ordinal=1))
    _append(log, session_id, TurnPosted(id="t-q1", actor=Actor.challenger, kind=TurnKind.question))
    _append(log, session_id, ArtifactAttached(id="a-q1", kind=ArtifactKind.prompt, produced_by_turn_id="t-q1", version=1, content="Problem one opener"))
    _append(log, session_id, TurnPosted(id="t-a1", actor=Actor.candidate, kind=TurnKind.answer))
    _append(log, session_id, ArtifactAttached(id="a-a1", kind=ArtifactKind.markdown, produced_by_turn_id="t-a1", version=1, content="Candidate answer one"))
    _append(log, session_id, ProblemClosed(problem_id=ProblemId("p1"), reason="examiner_pivot", rationale="move on"))
    _append(log, session_id, ProblemIntroduced(problem_id=ProblemId("p2"), opener_text="Problem two opener", ordinal=2))
    _append(log, session_id, TurnPosted(id="t-q2", actor=Actor.challenger, kind=TurnKind.question))
    _append(log, session_id, ArtifactAttached(id="a-q2", kind=ArtifactKind.prompt, produced_by_turn_id="t-q2", version=1, content="Problem two opener"))


def test_continuous_chat_renders_problem_progress_and_boundaries(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    session_id = "s-ui"
    _seed_two_problem_thread(log, session_id)
    app = make_app(log=log, runner=_runner(tmp_path), output_dir=tmp_path, voice_mode="on")
    client = TestClient(app)

    response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    assert "Problem 2 of 3" in response.text
    assert 'class="problem-divider"' in response.text
    assert "Problem 1" in response.text and "Problem 2" in response.text
    assert "Candidate answer one" in response.text
    assert response.text.count("Problem one opener") == 1
    assert response.text.count("Problem two opener") == 1
    assert 'class="composer-shell"' in response.text
    assert 'aria-label="Answer input"' in response.text


def test_probe_pending_uses_thinking_affordance_and_stream_target(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    session_id = "s-thinking"
    _append(log, session_id, SessionStarted(rubric_version="ds", target_answers=1, max_probes_per_prompt=1))
    _append(log, session_id, CandidateJoined(candidate_handle="alice"))
    _append(log, session_id, TurnPosted(id="t-q1", actor=Actor.challenger, kind=TurnKind.question))
    _append(log, session_id, ArtifactAttached(id="a-q1", kind=ArtifactKind.prompt, produced_by_turn_id="t-q1", version=1, content="Question?"))
    _append(log, session_id, TurnPosted(id="t-a1", actor=Actor.candidate, kind=TurnKind.answer))
    _append(log, session_id, ArtifactAttached(id="a-a1", kind=ArtifactKind.markdown, produced_by_turn_id="t-a1", version=1, content="Answer."))
    _append(log, session_id, TurnPosted(id="t-p1", actor=Actor.examiner, kind=TurnKind.probe))
    app = make_app(log=log, runner=_runner(tmp_path), output_dir=tmp_path)
    client = TestClient(app)

    response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    assert "Thinking" in response.text
    assert 'id="probe-stream-text"' in response.text
    assert 'aria-live="polite"' in response.text


def test_voice_switch_to_text_preserves_partial_transcript_in_answer_box() -> None:
    js = Path("adapters/http/static/voice.js").read_text(encoding="utf-8")

    assert "const partial = document.getElementById('voice-transcript')" in js
    assert "textarea.value = partial.textContent" in js


def test_chat_composer_prevents_stale_input_after_submit() -> None:
    html = Path("adapters/http/templates/turn.html").read_text(encoding="utf-8")

    assert 'autocomplete="off"' in html
    assert "composer.requestSubmit()" in html
    assert "composer.addEventListener('submit'" in html
    assert "answer.value = ''" in html
    assert "window.addEventListener('pageshow'" in html
