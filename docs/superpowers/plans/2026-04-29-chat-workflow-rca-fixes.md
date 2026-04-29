# Chat Workflow RCA Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the RCA-confirmed chat interview workflow correctness issues without weakening the event-sourced architecture.

**Architecture:** Treat every candidate/system mutation as an idempotent command that writes durable events. Keep the pure orchestrator pure, but make the adapter layer concurrency-safe, recoverable after partial writes, and explicit about modality/rubric scope. Streamed or fallback interviewer text must be persisted as canonical artifacts before the candidate can respond.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite event log, Pydantic events/projections, pytest, mypy, ruff.

---

## File Structure

- Modify: `adapters/http/app.py`
  - Add submit guards for unknown/ended/blank sessions.
  - Reuse the stored `TurnPosted` envelope on idempotent retries.
  - Stop rendering unpersisted probe streams as canonical interview text.
- Modify: `adapters/http/session_runner.py`
  - Add deterministic idempotency keys for system-generated events.
  - Persist/select problem plans once per session.
  - Emit problem coverage observations before examiner review.
  - Persist fallback examiner probes when provider/parsing fails.
  - Add problem context to examiner coverage prompts.
- Modify: `adapters/http/sse_routes.py`
  - Change SSE to stream already-persisted examiner artifact text only; no independent model calls.
- Modify: `adapters/http/templates/turn.html`
  - Remove model-calling EventSource behavior for pending probes; keep UI resilient for persisted/fallback text.
- Modify: `core/events.py`
  - Add `ProblemPlanSelected` and `ProblemCoverageObserved` events.
- Modify: `core/projections.py`
  - Project the selected problem plan and expose it to session replay.
- Modify: `core/problem_bank.py`
  - Preserve bank `version` and add lookup by `ProblemId`.
- Modify: `core/coverage.py`
  - Keep `CoverageTracker` as the in-memory reducer; feed it from `ProblemCoverageObserved` events.
- Modify: `adapters/examiner/llm_examiner.py`
  - Extend `CoverageContext` with problem context, target dimensions, and expected duration.
- Create: `adapters/scorer/llm_experiment_design_scorer.py`
  - LLM scorer for the rubric dimension currently present in the problem bank but absent from chat scoring.
- Create: `templates/agents/scorers/experiment_design/IDENTITY.md`
- Create: `templates/agents/scorers/experiment_design/SOUL.md`
- Create: `templates/agents/scorers/experiment_design/TOOLS.md`
- Create: `templates/rubrics/ds-ml-engineer-chat-v1.yaml`
  - Chat modality rubric excluding voice-only `response_authenticity` and including `experiment_design`.
- Modify: `adapters/http/app.py`
  - Load the chat rubric and configure all chat-scored dimensions.
- Add/modify tests:
  - Create: `tests/integration/test_chat_turn_submission_guards.py`
  - Create: `tests/integration/test_chat_advance_idempotency.py`
  - Create: `tests/integration/test_problem_plan_persistence.py`
  - Create: `tests/integration/test_probe_persistence.py`
  - Create: `tests/integration/test_problem_coverage_flow.py`
  - Modify: `tests/integration/test_problem_bank_session.py`
  - Modify: `tests/integration/test_continuous_chat_ui.py`
  - Modify: `tests/unit/test_problem_bank.py`
  - Modify: `tests/unit/test_events.py`
  - Modify: `tests/unit/test_examiner.py`
  - Modify: `tests/unit/test_scorers.py`

---

## Task 1: Candidate submit guard regression tests

**Files:**
- Create: `tests/integration/test_chat_turn_submission_guards.py`
- Read: `tests/integration/test_http_double_submit.py`
- Read: `tests/integration/test_e2e_crash_resume.py`

- [ ] **Step 1: Write tests for rejected unknown sessions, ended sessions, blank submissions, and retry recovery**

Add this file:

```python
from __future__ import annotations

import textwrap
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
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from core.domain import Actor, Dimension, TurnKind
from core.events import ArtifactAttached, Envelope, SessionEnded, TurnPosted
from core.rubric_loader import load_rubric

_MINI_RUBRIC = textwrap.dedent("""\
    name: test-submit-guards
    version: 1
    dimensions:
      - name: model_rationale
        weight: 0.5
      - name: communication
        weight: 0.5
    aggregation:
      min_signals_per_dimension: 1
      confidence_weighting: true
""")


def _app(tmp_path: Path) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    aggregator = RubricAggregator(load_rubric(yaml_str=_MINI_RUBRIC), output_dir=tmp_path)
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.communication: LlmCommunicationScorer(router),
        },
        aggregator=aggregator,
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )
    return TestClient(
        make_app(log=log, runner=runner, target_answers=1, max_probes=0, output_dir=tmp_path),
        follow_redirects=False,
    ), log


def _create_session(client: TestClient) -> str:
    response = client.post("/sessions", data={"candidate_handle": "alice"})
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def test_unknown_session_turn_post_is_rejected_without_events(tmp_path: Path) -> None:
    client, log = _app(tmp_path)

    response = client.post(
        "/sessions/not-a-session/turn",
        data={"answer": "injected", "code": "", "turn_nonce": "bad"},
    )

    assert response.status_code == 404
    assert log.get_session("not-a-session") == ()


def test_ended_session_turn_post_redirects_to_result_without_mutating_log(tmp_path: Path) -> None:
    client, log = _app(tmp_path)
    session_id = _create_session(client)
    client = TestClient(client.app, follow_redirects=True)
    client.get(f"/sessions/{session_id}")
    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "model rationale", "code": "", "turn_nonce": "n1"},
    )
    before = log.get_session(session_id)
    assert any(isinstance(env.payload, SessionEnded) for env in before)

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "late mutation", "code": "", "turn_nonce": "late"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/sessions/{session_id}/result"
    assert log.get_session(session_id) == before


def test_blank_turn_post_is_rejected_without_candidate_turn(tmp_path: Path) -> None:
    client, log = _app(tmp_path)
    session_id = _create_session(client)
    client.get(f"/sessions/{session_id}")

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "   ", "code": "   ", "turn_nonce": "blank"},
    )

    assert response.status_code == 400
    candidate_turns = [
        env for env in log.get_session(session_id)
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.candidate
    ]
    assert candidate_turns == []


def test_retry_after_partial_turn_write_attaches_artifact_to_original_turn(tmp_path: Path) -> None:
    client, log = _app(tmp_path)
    session_id = _create_session(client)
    client.get(f"/sessions/{session_id}")
    original_turn_id = "t-original"
    log.append(
        Envelope(
            session_id=session_id,
            seq=(log.last_seq(session_id) or 0) + 1,
            at=datetime.now(UTC),
            payload=TurnPosted(id=original_turn_id, actor=Actor.candidate, kind=TurnKind.answer),
            idem_key="recoverable",
        ),
        "recoverable",
    )

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "recovered answer", "code": "", "turn_nonce": "recoverable"},
    )

    assert response.status_code == 303
    artifacts = [
        env.payload for env in log.get_session(session_id)
        if isinstance(env.payload, ArtifactAttached) and env.payload.content == "recovered answer"
    ]
    assert len(artifacts) == 1
    assert artifacts[0].produced_by_turn_id == original_turn_id
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_chat_turn_submission_guards.py
```

Expected: failures showing current behavior accepts unknown sessions, mutates ended sessions, accepts blank submissions, and orphan-links retry artifacts.

---

## Task 2: Guard and recover candidate turn submission

**Files:**
- Modify: `adapters/http/app.py:265-368`
- Test: `tests/integration/test_chat_turn_submission_guards.py`

- [ ] **Step 1: Add guarded submit helpers in `app.py`**

Add `HTTPException` to the FastAPI imports:

```python
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
```

Add this helper above `make_app()`:

```python
def _session_record_or_404(session_id: str, log: EventLog) -> dict[str, object]:
    sessions, _, _, _, _ = replay(session_id, log)
    record = sessions.get(session_id)
    if record is None or record.get("started_at") is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return cast(dict[str, object], record)
```

- [ ] **Step 2: Change `post_turn()` to reject unknown, ended, and blank submissions before writing**

Inside `post_turn()`, before generating `turn_id`, add:

```python
record = _session_record_or_404(session_id, _log)
if bool(record.get("ended")):
    return RedirectResponse(f"/sessions/{session_id}/result", status_code=303)

answer_text = answer.strip()
code_text = code.strip()
if not answer_text and not code_text:
    raise HTTPException(status_code=400, detail="answer or code is required")
```

Remove the later duplicate assignments of `answer_text` and `code_text`.

- [ ] **Step 3: Use the stored `TurnPosted` envelope on idempotent retries**

Replace the candidate turn append block with:

```python
turn_env = _log.append(
    Envelope(
        session_id=session_id,
        seq=seq,
        at=now,
        payload=TurnPosted(id=turn_id, actor=Actor.candidate, kind=kind),
        idem_key=nonce,
    ),
    nonce,
)
stored_turn = cast(TurnPosted, turn_env.payload)
turn_id = stored_turn.id
```

This is the key recovery invariant: all subsequent artifacts link to the persisted turn ID, not the freshly generated retry ID.

- [ ] **Step 4: Verify GREEN for submit guards**

Run:

```bash
rtk uv run pytest -q tests/integration/test_chat_turn_submission_guards.py tests/integration/test_http_double_submit.py tests/integration/test_e2e_crash_resume.py
```

Expected: all selected tests pass.

---

## Task 3: System advancement idempotency and concurrent GET safety

**Files:**
- Create: `tests/integration/test_chat_advance_idempotency.py`
- Modify: `adapters/http/session_runner.py:257-486`

- [ ] **Step 1: Add concurrent advancement regression tests**

Create `tests/integration/test_chat_advance_idempotency.py`:

```python
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Actor, Problem, ProblemId
from core.events import ProblemIntroduced, TurnPosted


def _runner(tmp_path: Path, *, problems: list[Problem] | None = None) -> SessionRunner:
    router = ModelRouter(FakeRouter())
    return SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=RubricAggregator.from_yaml(
            Path("templates/rubrics/ds-ml-engineer-v1.yaml"),
            output_dir=tmp_path,
        ),
        problems=problems or [],
    )


def _new_session_no_follow(client: TestClient) -> str:
    response = client.post("/sessions", data={"candidate_handle": "race"})
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def _concurrent_gets(app: object, session_id: str) -> None:
    def get_once() -> int:
        client = TestClient(app, raise_server_exceptions=True)
        return client.get(f"/sessions/{session_id}").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(get_once), pool.submit(get_once)]
        statuses = [future.result() for future in as_completed(futures)]
    assert statuses == [200, 200]


def test_concurrent_legacy_get_introduces_one_challenger_turn(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    app = make_app(log=log, runner=_runner(tmp_path), output_dir=tmp_path)
    session_id = _new_session_no_follow(TestClient(app, follow_redirects=False))

    _concurrent_gets(app, session_id)

    challenger_turns = [
        env.payload for env in log.get_session(session_id)
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.challenger
    ]
    assert len(challenger_turns) == 1


def test_concurrent_problem_get_introduces_one_problem_and_opener(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    app = make_app(
        log=log,
        runner=_runner(tmp_path, problems=[Problem(id=ProblemId("p1"), opener_text="P1")]),
        output_dir=tmp_path,
    )
    session_id = _new_session_no_follow(TestClient(app, follow_redirects=False))

    _concurrent_gets(app, session_id)

    introduced = [env for env in log.get_session(session_id) if isinstance(env.payload, ProblemIntroduced)]
    challenger_turns = [
        env.payload for env in log.get_session(session_id)
        if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.challenger
    ]
    assert len(introduced) == 1
    assert len(challenger_turns) == 1
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_chat_advance_idempotency.py
```

Expected: `test_concurrent_legacy_get_introduces_one_challenger_turn` fails because duplicate challenger turns are currently possible.

- [ ] **Step 3: Add deterministic idempotency keys for system writes**

In `SessionRunner`, add a helper near `_append()`:

```python
    def _append_turn_with_artifact(
        self,
        session_id: str,
        log: EventLog,
        *,
        turn: TurnPosted,
        artifact: ArtifactAttached,
        turn_key: str,
        artifact_key: str,
    ) -> str:
        turn_env = self._append(session_id, log, turn, idem_key=turn_key)
        stored_turn = cast(TurnPosted, turn_env.payload)
        artifact = ArtifactAttached(
            id=artifact.id,
            kind=artifact.kind,
            produced_by_turn_id=stored_turn.id,
            version=artifact.version,
            content=artifact.content,
        )
        self._append(session_id, log, artifact, idem_key=artifact_key)
        return stored_turn.id
```

Add `cast` to imports:

```python
from typing import Literal, cast
```

Use idempotency keys in these methods:

```python
# _do_introduce_problem
idem_key=f"problem-introduced:{problem.id}"
turn_key=f"problem-opener-turn:{problem.id}"
artifact_key=f"problem-opener-artifact:{problem.id}"
timing_key=f"problem-opener-timing:{problem.id}"

# _do_challenge
challenge_index = 1 + sum(
    1 for env in log.get_session(session_id)
    if isinstance(env.payload, TurnPosted) and env.payload.actor == Actor.challenger
)
turn_key=f"challenge-turn:{challenge_index}"
artifact_key=f"challenge-artifact:{challenge_index}"

# _do_stage_challenge
turn_key=f"stage-turn:{stage_id}"
artifact_key=f"stage-artifact:{stage_id}"

# _do_close_problem
idem_key=f"problem-closed:{problem_id}"

# _do_probe
turn_key=f"probe-after:{last_candidate_turn_id}"
artifact_key=f"probe-artifact-after:{last_candidate_turn_id}"
timing_key=f"probe-timing-after:{last_candidate_turn_id}"

# _do_aggregate
idem_key="score-computed"
idem_key="session-ended:scored"
```

For `_do_probe`, compute `last_candidate_turn_id` from the replayed session record and use it in the keys. If no candidate turn exists, use `last_candidate_turn_id = "none"`.

- [ ] **Step 4: Verify GREEN for concurrent advancement**

Run:

```bash
rtk uv run pytest -q tests/integration/test_chat_advance_idempotency.py tests/integration/test_e2e_concurrent.py
```

Expected: all selected tests pass.

---

## Task 4: Persist selected problem plan per session

**Files:**
- Modify: `core/events.py`
- Modify: `core/projections.py`
- Modify: `core/problem_bank.py`
- Modify: `adapters/http/session_runner.py`
- Create: `tests/integration/test_problem_plan_persistence.py`
- Modify: `tests/unit/test_problem_bank.py`
- Modify: `tests/unit/test_events.py`

- [ ] **Step 1: Add RED tests for stable problem plan selection**

Create `tests/integration/test_problem_plan_persistence.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Problem, ProblemId
from core.events import ProblemIntroduced, ProblemPlanSelected
from core.problem_bank import ProblemBank, ProblemBankEntry


def _bank(version: str, problems: list[Problem]) -> ProblemBank:
    return ProblemBank([ProblemBankEntry(problem=p, tags=()) for p in problems], version=version)


def _runner(tmp_path: Path, bank: ProblemBank) -> SessionRunner:
    router = ModelRouter(FakeRouter())
    return SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=RubricAggregator.from_yaml(Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path),
        problem_bank=bank,
    )


def test_problem_plan_is_selected_once_and_survives_bank_reorder(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    p1 = Problem(id=ProblemId("p1"), opener_text="First")
    p2 = Problem(id=ProblemId("p2"), opener_text="Second")
    app = make_app(log=log, runner=_runner(tmp_path, _bank("v1", [p1, p2])), output_dir=tmp_path)
    client = TestClient(app, follow_redirects=True)
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]

    runner: SessionRunner = app.state.runner
    runner.problem_bank = _bank("v2", [p2, p1])
    client.get(f"/sessions/{session_id}")

    events = [env.payload for env in log.get_session(session_id)]
    plans = [event for event in events if isinstance(event, ProblemPlanSelected)]
    introduced = [event for event in events if isinstance(event, ProblemIntroduced)]
    assert len(plans) == 1
    assert tuple(plans[0].problem_ids) == tuple(problem.id for problem in _bank("v1", [p1, p2]).pick_sequence(session_id))
    assert introduced[0].problem_id == plans[0].problem_ids[0]
```

- [ ] **Step 2: Run problem-plan test to verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_problem_plan_persistence.py
```

Expected: import or assertion failure because `ProblemPlanSelected` and durable plan projection do not exist yet.

- [ ] **Step 3: Add `ProblemPlanSelected` event**

In `core/events.py`, add:

```python
class ProblemPlanSelected(_Evt):
    problem_ids: tuple[ProblemId, ...]
    bank_version: str | None = None
    source: Literal["explicit", "problem_bank"]
```

Add it to `EVENT_TYPES`.

- [ ] **Step 4: Preserve problem-bank version and lookup by ID**

Change `ProblemBank.__init__` in `core/problem_bank.py` to:

```python
def __init__(self, entries: Sequence[ProblemBankEntry], *, version: str = "unknown") -> None:
    ...
    self.version = version
    self._by_id: dict[ProblemId, Problem] = {entry.problem.id: entry.problem for entry in self._entries}
```

In `from_yaml()`, pass the YAML version:

```python
return cls(entries, version=str(raw.get("version") or "unknown"))
```

Add:

```python
def get(self, problem_id: ProblemId) -> Problem | None:
    return self._by_id.get(problem_id)
```

Update tests that instantiate `ProblemBank(...)` directly to pass no version; default keeps them valid.

- [ ] **Step 5: Project selected problem plan**

In `core/projections.py`, add to `_SessionRecord`:

```python
planned_problem_ids: tuple[ProblemId, ...]
problem_bank_version: str | None
```

Add defaults in `_new_record()`:

```python
planned_problem_ids=(),
problem_bank_version=None,
```

Handle the event in `SessionStore.apply()`:

```python
elif isinstance(payload, ProblemPlanSelected):
    b = self._bucket(sid)
    if not b["planned_problem_ids"]:
        b["planned_problem_ids"] = payload.problem_ids
        b["problem_bank_version"] = payload.bank_version
```

- [ ] **Step 6: Make `SessionRunner` select/read the durable problem plan**

Change `_planned_problems()` to accept `log` and `session_record`:

```python
def _planned_problems(self, session_id: str, log: EventLog, session_record: object | None) -> list[Problem]:
    if self.problems:
        return list(self.problems)
    if self.problem_bank is None:
        return []
    if isinstance(session_record, dict) and session_record.get("planned_problem_ids"):
        out: list[Problem] = []
        for pid in session_record["planned_problem_ids"]:
            problem = self.problem_bank.get(ProblemId(str(pid)))
            if problem is not None:
                out.append(problem)
        return out
    selected = self.problem_bank.pick_sequence(session_id)
    self._append(
        session_id,
        log,
        ProblemPlanSelected(
            problem_ids=tuple(problem.id for problem in selected),
            bank_version=self.problem_bank.version,
            source="problem_bank",
        ),
        idem_key="problem-plan-selected",
    )
    return selected
```

At the top of `advance()`, replay before computing planned problems, then compute `planned_problems` from the current record. After appending a new plan, continue the loop so projections are refreshed before sequencing.

- [ ] **Step 7: Verify problem-plan GREEN**

Run:

```bash
rtk uv run pytest -q tests/integration/test_problem_plan_persistence.py tests/unit/test_problem_bank.py tests/unit/test_events.py tests/integration/test_problem_bank_session.py
```

Expected: all selected tests pass.

---

## Task 5: Persist examiner fallback text and remove unpersisted model-stream behavior

**Files:**
- Create: `tests/integration/test_probe_persistence.py`
- Modify: `adapters/http/session_runner.py:378-486`
- Modify: `adapters/http/sse_routes.py`
- Modify: `adapters/http/templates/turn.html`
- Modify: `tests/integration/test_continuous_chat_ui.py`

- [ ] **Step 1: Add RED tests for examiner failure and SSE persistence**

Create `tests/integration/test_probe_persistence.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import ExaminerOutcome
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Actor, Problem, ProblemId, TurnKind
from core.events import ArtifactAttached, ExaminerFailed, TurnPosted


class FailingExaminer:
    def review(self, session_id: str, recent_turns: list[Any], **kwargs: Any) -> tuple[ExaminerOutcome, ExaminerFailed]:
        del session_id, recent_turns, kwargs
        return ExaminerOutcome(ok_to_advance=True), ExaminerFailed(reason="provider down")

    def iter_review(self, *args: Any, **kwargs: Any):
        raise AssertionError("probe-stream must not call the model")


def _app(tmp_path: Path, examiner: object) -> tuple[TestClient, SqliteEventLog]:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        examiner=examiner,  # type: ignore[arg-type]
        aggregator=RubricAggregator.from_yaml(Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path),
        problems=[Problem(id=ProblemId("p1"), opener_text="P1")],
    )
    return TestClient(make_app(log=log, runner=runner, output_dir=tmp_path), follow_redirects=True), log


def test_examiner_failure_posts_visible_fallback_probe_artifact(tmp_path: Path) -> None:
    client, log = _app(tmp_path, FailingExaminer())
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]

    response = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would define the metric first.", "turn_nonce": "n1"},
    )

    assert response.status_code == 200
    assert "Please clarify your assumptions" in response.text
    events = [env.payload for env in log.get_session(session_id)]
    probe_turns = [event for event in events if isinstance(event, TurnPosted) and event.actor == Actor.examiner and event.kind == TurnKind.probe]
    probe_artifacts = [event for event in events if isinstance(event, ArtifactAttached) and event.produced_by_turn_id == probe_turns[-1].id]
    assert len(probe_artifacts) == 1
    assert "Please clarify your assumptions" in (probe_artifacts[0].content or "")


def test_probe_stream_streams_persisted_text_without_model_call(tmp_path: Path) -> None:
    client, log = _app(tmp_path, FailingExaminer())
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]
    client.post(f"/sessions/{session_id}/turn", data={"answer": "Answer", "turn_nonce": "n1"})

    response = client.get(f"/sessions/{session_id}/probe-stream")

    assert response.status_code == 200
    assert "Please clarify your assumptions" in response.text
```

- [ ] **Step 2: Run probe tests to verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_probe_persistence.py
```

Expected: tests fail because the failure path posts an empty probe and SSE calls the model independently.

- [ ] **Step 3: Add deterministic fallback text in `_do_probe()`**

In `SessionRunner._do_probe()`, after examiner review and before posting the probe turn, add:

```python
if failure is not None and not probe_text:
    probe_text = (
        "Please clarify your assumptions and the next concrete step you would take "
        "before committing to a model or recommendation."
    )
```

Keep appending `ExaminerFailed` for observability.

- [ ] **Step 4: Make `/probe-stream` stream persisted text only**

Replace the generator in `adapters/http/sse_routes.py` with logic that replays artifacts, finds the latest `TurnPosted(actor=examiner, kind=probe)` with an artifact, and yields that artifact content. Do not call `examiner.iter_review()`.

Use this shape:

```python
sessions, _, _, _, artifacts = replay(session_id, log)
s = sessions.get(session_id)
latest_text = ""
if s is not None:
    for turn in reversed(s["turns"]):
        if turn["actor"].value == "examiner" and turn["kind"].value == "probe":
            for aid, tid in s["artifact_turn"].items():
                if tid == turn["id"]:
                    latest_text = artifacts.get(aid) or ""
                    break
            break

def _gen() -> Iterator[bytes]:
    if latest_text:
        yield f"data: {latest_text}\n\n".encode()
    yield b"event: done\ndata: [DONE]\n\n"
```

- [ ] **Step 5: Keep template behavior simple**

In `turn.html`, remove the inline `EventSource` model-stream script. Keep the “Thinking…” affordance only for legacy pending probe records, and refresh via normal page loads after persisted examiner artifacts exist.

- [ ] **Step 6: Verify probe persistence GREEN**

Run:

```bash
rtk uv run pytest -q tests/integration/test_probe_persistence.py tests/integration/test_continuous_chat_ui.py tests/integration/test_problem_bank_session.py
```

Expected: all selected tests pass after updating any old assertions that expected model-calling streaming.

---

## Task 6: Problem coverage observations before examiner decisions

**Files:**
- Modify: `core/events.py`
- Modify: `core/coverage.py`
- Modify: `adapters/http/session_runner.py`
- Create: `tests/integration/test_problem_coverage_flow.py`
- Modify: `tests/unit/test_events.py`

- [ ] **Step 1: Add RED test proving examiner receives non-empty coverage from latest answer**

Create `tests/integration/test_problem_coverage_flow.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.examiner.llm_examiner import CoverageContext, ExaminerOutcome
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, Problem, ProblemId
from core.events import ProblemCoverageObserved


class CapturingExaminer:
    def __init__(self) -> None:
        self.coverages: list[CoverageContext] = []

    def review(self, session_id: str, recent_turns: list[Any], **kwargs: Any) -> tuple[ExaminerOutcome, None]:
        del session_id, recent_turns
        self.coverages.append(kwargs["coverage"])
        return ExaminerOutcome(action="probe", probe_text="What metric would you inspect first?", ok_to_advance=False), None


def test_latest_candidate_answer_emits_problem_coverage_before_examiner_review(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    examiner = CapturingExaminer()
    problem = Problem(
        id=ProblemId("p1"),
        opener_text="Frame churn.",
        context="Look for metric definition and cohort/data quality risks.",
        target_dimensions=(Dimension.problem_framing, Dimension.communication),
        dim_thresholds={Dimension.problem_framing: 0.5, Dimension.communication: 0.5},
    )
    app = make_app(
        log=log,
        runner=SessionRunner(
            challenger=LlmChallenger(router),
            examiner=examiner,  # type: ignore[arg-type]
            aggregator=RubricAggregator.from_yaml(Path("templates/rubrics/ds-ml-engineer-v1.yaml"), output_dir=tmp_path),
            problems=[problem],
        ),
        output_dir=tmp_path,
    )
    client = TestClient(app, follow_redirects=True)
    created = client.post("/sessions", data={"candidate_handle": "alice"})
    session_id = created.url.path.rsplit("/", 1)[-1]

    client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "I would define the churn metric, cohort, and stakeholder decision.", "turn_nonce": "n1"},
    )

    coverage_events = [env.payload for env in log.get_session(session_id) if isinstance(env.payload, ProblemCoverageObserved)]
    assert {event.dimension for event in coverage_events} == {Dimension.problem_framing, Dimension.communication}
    assert examiner.coverages
    assert examiner.coverages[0].signal_map[Dimension.problem_framing.value] > 0
    assert examiner.coverages[0].problem_context == problem.context
```

- [ ] **Step 2: Run coverage test to verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_problem_coverage_flow.py
```

Expected: import or assertion failure because coverage observation events and context fields do not exist.

- [ ] **Step 3: Add `ProblemCoverageObserved` event**

In `core/events.py`, add:

```python
class ProblemCoverageObserved(_Evt):
    problem_id: ProblemId
    artifact_id: str
    dimension: Dimension
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["coverage_heuristic"] = "coverage_heuristic"
```

Add it to `EVENT_TYPES`.

- [ ] **Step 4: Emit coverage observations idempotently before examiner review**

In `SessionRunner._do_probe()`, before `_build_coverage_tracker()`, call a new helper:

```python
self._emit_problem_coverage_for_latest_candidate(session_id, log, current_problem_id)
tracker = _build_coverage_tracker(session_id, log)
```

Add helper methods:

```python
    def _emit_problem_coverage_for_latest_candidate(
        self,
        session_id: str,
        log: EventLog,
        problem_id: str | None,
    ) -> None:
        if problem_id is None:
            return
        sessions, _, _, _, artifacts = _replay_minimal(session_id, log)
        s = sessions.get(session_id)
        if s is None:
            return
        problem = _find_problem(self._planned_problems(session_id, log, s), problem_id)
        dimensions = tuple(problem.target_dimensions) if problem and problem.target_dimensions else self.scored_dimensions
        candidate_turn_ids = [turn["id"] for turn in s["turns"] if turn["actor"] == Actor.candidate]
        if not candidate_turn_ids:
            return
        latest_turn_id = candidate_turn_ids[-1]
        for artifact_id, turn_id in s["artifact_turn"].items():
            if turn_id != latest_turn_id or s["artifact_kind"].get(artifact_id) != ArtifactKind.markdown:
                continue
            text = artifacts.get(artifact_id) or ""
            for dimension in dimensions:
                value = _heuristic_coverage_value(dimension, text)
                self._append(
                    session_id,
                    log,
                    ProblemCoverageObserved(
                        problem_id=ProblemId(problem_id),
                        artifact_id=artifact_id,
                        dimension=dimension,
                        value=value,
                        confidence=0.35,
                    ),
                    idem_key=f"coverage:{problem_id}:{artifact_id}:{dimension.value}",
                )
```

Add module-level heuristic:

```python
def _heuristic_coverage_value(dimension: Dimension, text: str) -> float:
    lower = text.lower()
    keywords: dict[Dimension, tuple[str, ...]] = {
        Dimension.problem_framing: ("metric", "goal", "cohort", "define", "scope", "business"),
        Dimension.model_rationale: ("model", "baseline", "feature", "trade-off", "because"),
        Dimension.experiment_design: ("experiment", "validation", "holdout", "test", "power"),
        Dimension.insight_interp: ("interpret", "result", "trend", "segment", "uncertainty"),
        Dimension.communication: ("stakeholder", "recommend", "explain", "decision", "risk"),
        Dimension.response_authenticity: ("assumption", "clarify", "example", "specific"),
    }
    hits = sum(1 for keyword in keywords.get(dimension, ()) if keyword in lower)
    return min(1.0, 0.2 + (0.2 * hits)) if hits else 0.0
```

- [ ] **Step 5: Rebuild coverage from `ProblemCoverageObserved`**

Change `_build_coverage_tracker()` to record `ProblemCoverageObserved` events:

```python
for env in log.get_session(session_id):
    if isinstance(env.payload, ProblemCoverageObserved):
        obs = env.payload
        tracker.record(obs.problem_id, obs.dimension, obs.value)
```

Keep the old bracketed `SignalEmitted` fallback only for historical sessions.

- [ ] **Step 6: Verify coverage GREEN**

Run:

```bash
rtk uv run pytest -q tests/integration/test_problem_coverage_flow.py tests/unit/test_events.py tests/unit/test_coverage.py tests/integration/test_problem_bank_session.py
```

Expected: all selected tests pass.

---

## Task 7: Add problem context to examiner prompts

**Files:**
- Modify: `adapters/examiner/llm_examiner.py`
- Modify: `adapters/http/session_runner.py`
- Modify: `tests/unit/test_examiner.py`
- Covered by: `tests/integration/test_problem_coverage_flow.py`

- [ ] **Step 1: Add unit test for context formatting**

In `tests/unit/test_examiner.py`, add:

```python
def test_coverage_context_includes_problem_guidance_in_prompt() -> None:
    router = _RouterReturning({"action": "probe", "text": "Why?", "rationale": "need more"})
    examiner = LlmExaminer(router)
    coverage = CoverageContext(
        problem_id="p1",
        under_served_dims=("problem_framing",),
        signal_map={"problem_framing": 0.4},
        probe_count=1,
        max_probes=6,
        problem_transcript="Candidate: I would define churn.",
        problem_context="Look for metric clarity and cohort definition.",
        target_dimensions=("problem_framing", "communication"),
        expected_duration_s=420,
    )

    examiner.review("s1", [], coverage=coverage)

    prompt = router.last_prompt
    assert "Look for metric clarity and cohort definition." in prompt
    assert "Target dimensions: problem_framing, communication" in prompt
    assert "Expected duration: 420s" in prompt
```

Use the existing fake router helper names in `tests/unit/test_examiner.py`; if the helper is named differently, adapt the test to the existing helper without changing behavior.

- [ ] **Step 2: Run examiner context test to verify RED**

Run:

```bash
rtk uv run pytest -q tests/unit/test_examiner.py::test_coverage_context_includes_problem_guidance_in_prompt
```

Expected: fails because the fields are absent from `CoverageContext` / prompt formatting.

- [ ] **Step 3: Extend `CoverageContext`**

In `LlmExaminer`:

```python
@dataclass(frozen=True)
class CoverageContext:
    problem_id: str = ""
    under_served_dims: tuple[str, ...] = ()
    signal_map: dict[str, float] = field(default_factory=dict)
    probe_count: int = 0
    max_probes: int = 6
    problem_transcript: str = ""
    problem_context: str = ""
    target_dimensions: tuple[str, ...] = ()
    expected_duration_s: int = 0
```

Update `_format_coverage_context()`:

```python
if ctx.problem_context:
    lines.append("Problem guidance: " + ctx.problem_context)
if ctx.target_dimensions:
    lines.append("Target dimensions: " + ", ".join(ctx.target_dimensions))
if ctx.expected_duration_s:
    lines.append(f"Expected duration: {ctx.expected_duration_s}s")
```

- [ ] **Step 4: Populate the new fields in `SessionRunner._do_probe()`**

When creating `CoverageContext`, include:

```python
problem_context=problem_obj.context if problem_obj else "",
target_dimensions=tuple(d.value for d in (problem_obj.target_dimensions if problem_obj else ())),
expected_duration_s=problem_obj.expected_duration_s if problem_obj else 0,
```

- [ ] **Step 5: Verify examiner context GREEN**

Run:

```bash
rtk uv run pytest -q tests/unit/test_examiner.py tests/integration/test_problem_coverage_flow.py
```

Expected: all selected tests pass.

---

## Task 8: Align chat rubric and scorer registry

**Files:**
- Create: `adapters/scorer/llm_experiment_design_scorer.py`
- Create: `templates/agents/scorers/experiment_design/IDENTITY.md`
- Create: `templates/agents/scorers/experiment_design/SOUL.md`
- Create: `templates/agents/scorers/experiment_design/TOOLS.md`
- Create: `templates/rubrics/ds-ml-engineer-chat-v1.yaml`
- Modify: `adapters/http/app.py:602-631`
- Modify: `tests/unit/test_scorers.py`
- Modify: `tests/integration/test_problem_bank_session.py`

- [ ] **Step 1: Add RED test for production chat scorer/rubric alignment**

In `tests/integration/test_problem_bank_session.py`, add:

```python
def test_create_app_chat_rubric_matches_scored_dimensions(tmp_path: Path) -> None:
    app = create_app(db_path=str(tmp_path / "app.db"))
    runner: SessionRunner = app.state.runner

    assert set(runner.scored_dimensions) == set(runner.aggregator.rubric.weights)
    assert set(runner.scorers) == set(runner.scored_dimensions)
    assert Dimension.experiment_design in runner.scored_dimensions
    assert Dimension.response_authenticity not in runner.scored_dimensions
```

- [ ] **Step 2: Run alignment test to verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_problem_bank_session.py::test_create_app_chat_rubric_matches_scored_dimensions
```

Expected: fails because `experiment_design` is missing and the loaded rubric contains `response_authenticity`.

- [ ] **Step 3: Create experiment-design scorer**

Create `adapters/scorer/llm_experiment_design_scorer.py`:

```python
from __future__ import annotations

from adapters.scorer._base import BaseLlmScorer
from core.domain import Dimension


class LlmExperimentDesignScorer(BaseLlmScorer):
    dimension = Dimension.experiment_design
    scorer_name = "scorer.experiment_design"
    template_dir_name = "experiment_design"
    heuristic_keywords = (
        "experiment",
        "validation",
        "holdout",
        "test",
        "guardrail",
        "power",
        "sample",
        "metric",
    )
    heuristic_hit_value = 0.5
    heuristic_miss_value = 0.2
```

- [ ] **Step 4: Add scorer persona templates**

Create `templates/agents/scorers/experiment_design/IDENTITY.md`:

```markdown
# Experiment Design Scorer

You score whether the candidate proposes a practical validation or experiment plan that can test the stated hypothesis under real constraints.
```

Create `templates/agents/scorers/experiment_design/SOUL.md`:

```markdown
Reward concrete validation strategy, guardrail metrics, failure modes, operational constraints, and clear links between the experiment and the business decision. Penalize vague "run an A/B test" answers with no metric hierarchy, sample/power thinking, or rollout risk controls.
```

Create `templates/agents/scorers/experiment_design/TOOLS.md`:

```markdown
Return JSON with a `signals` array. Each signal must include `dimension`, `value`, `confidence`, and `source_refs`. Use dimension `experiment_design` only.
```

- [ ] **Step 5: Add chat-specific rubric**

Create `templates/rubrics/ds-ml-engineer-chat-v1.yaml`:

```yaml
name: ds-ml-engineer-chat
version: 1
role: "Data Scientist / ML Engineer"
description: >
  Chat-mode rubric. Five dimensions. Voice-only response authenticity is excluded
  from chat sessions and scored only by voice-specific workflows.

dimensions:
  - name: problem_framing
    weight: 0.2045
    description: >
      Can the candidate translate the ambiguous business prompt into a concrete,
      measurable data science task — naming the target, the unit of analysis,
      and the success metric?

  - name: model_rationale
    weight: 0.25
    description: >
      Does the candidate justify modeling and feature choices against data and
      stakeholder decisions? Trade-offs named; simple-first tried or explicitly ruled out.

  - name: experiment_design
    weight: 0.1705
    description: >
      Can the candidate describe an execution plan that would actually test the
      hypothesis: validation strategy, failure modes, and operational trade-offs?

  - name: insight_interpretation
    weight: 0.1705
    description: >
      Do the candidate's stated conclusions correctly match what their model
      and charts actually show? Claims anchored in outputs, not in hope.

  - name: communication
    weight: 0.2045
    description: >
      Can a non-technical stakeholder read the candidate's summary and know
      what to do? Recommendation led; jargon glossed; uncertainty named.

aggregation:
  composite: weighted_mean
  min_signals_per_dimension: 1
  confidence_weighting: true
```

- [ ] **Step 6: Wire chat rubric/scorer in `create_app()`**

In `app.py`, import:

```python
from adapters.scorer.llm_experiment_design_scorer import LlmExperimentDesignScorer
```

Change rubric path:

```python
rubric_path = _Path("templates") / "rubrics" / "ds-ml-engineer-chat-v1.yaml"
```

Add scorer and scored dimension:

```python
Dimension.experiment_design: LlmExperimentDesignScorer(router),
```

Set `scored_dimensions` to exactly:

```python
(
    Dimension.problem_framing,
    Dimension.model_rationale,
    Dimension.experiment_design,
    Dimension.insight_interp,
    Dimension.communication,
)
```

- [ ] **Step 7: Add scorer unit coverage**

In `tests/unit/test_scorers.py`, add a test mirroring the existing scorer subclass tests:

```python
def test_experiment_design_scorer_scores_expected_dimension() -> None:
    router = _RouterReturning({
        "signals": [{
            "dimension": "experiment_design",
            "value": 0.7,
            "confidence": 0.8,
            "source_refs": ["a1"],
        }]
    })
    scorer = LlmExperimentDesignScorer(router)
    artifact = _artifact("a1", "I would use a holdout validation and guardrail metrics.")

    signal = scorer.score("s1", artifact, Dimension.experiment_design)

    assert signal.dimension == Dimension.experiment_design
    assert signal.value == 0.7
```

Use the existing fake router/helper names in `tests/unit/test_scorers.py`.

- [ ] **Step 8: Verify scorer/rubric alignment GREEN**

Run:

```bash
rtk uv run pytest -q tests/unit/test_scorers.py tests/integration/test_problem_bank_session.py::test_create_app_chat_rubric_matches_scored_dimensions
```

Expected: all selected tests pass.

---

## Task 9: Update documentation and task notes

**Files:**
- Modify: `README.md`
- Modify: `docs/tasks/todo.md`

- [ ] **Step 1: Update README architecture/runtime notes**

In `README.md`, update the candidate-flow and rubric language:

```markdown
Chat sessions use `templates/rubrics/ds-ml-engineer-chat-v1.yaml`, which scores five chat-observable dimensions. Voice-only `response_authenticity` remains part of the voice workflow and is not included in chat composites.
```

Add a short operational invariant:

```markdown
Candidate turn submission is idempotent by `turn_nonce`; retries after partial writes reuse the originally stored turn ID so artifacts cannot become orphaned. System-generated interviewer turns use deterministic idempotency keys so refreshes/concurrent GETs do not duplicate prompts.
```

- [ ] **Step 2: Add implementation review section to `docs/tasks/todo.md`**

After implementation, append exact verification commands and results under the RCA task review.

- [ ] **Step 3: Verify docs render as plain Markdown**

Run:

```bash
rtk python - <<'PY'
from pathlib import Path
for path in [Path('README.md'), Path('docs/tasks/todo.md')]:
    text = path.read_text()
    forbidden = ('T' + 'BD', 'TO' + 'DO')
    assert all(token not in text for token in forbidden)
PY
```

Expected: command exits 0.

---

## Task 10: Full verification gate

**Files:**
- No production files modified in this task.

- [ ] **Step 1: Run focused regression suites**

Run:

```bash
rtk uv run pytest -q \
  tests/integration/test_chat_turn_submission_guards.py \
  tests/integration/test_chat_advance_idempotency.py \
  tests/integration/test_problem_plan_persistence.py \
  tests/integration/test_probe_persistence.py \
  tests/integration/test_problem_coverage_flow.py \
  tests/integration/test_problem_bank_session.py \
  tests/integration/test_continuous_chat_ui.py \
  tests/integration/test_http_double_submit.py \
  tests/integration/test_e2e_crash_resume.py \
  tests/integration/test_e2e_concurrent.py \
  tests/unit/test_events.py \
  tests/unit/test_problem_bank.py \
  tests/unit/test_examiner.py \
  tests/unit/test_scorers.py \
  tests/unit/test_coverage.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run static checks on touched Python files**

Run:

```bash
rtk uv run ruff check \
  adapters/http/app.py \
  adapters/http/session_runner.py \
  adapters/http/sse_routes.py \
  adapters/examiner/llm_examiner.py \
  adapters/scorer/llm_experiment_design_scorer.py \
  core/events.py \
  core/projections.py \
  core/problem_bank.py \
  core/coverage.py \
  tests/integration/test_chat_turn_submission_guards.py \
  tests/integration/test_chat_advance_idempotency.py \
  tests/integration/test_problem_plan_persistence.py \
  tests/integration/test_probe_persistence.py \
  tests/integration/test_problem_coverage_flow.py \
  tests/unit/test_examiner.py \
  tests/unit/test_scorers.py
```

Expected: pass.

- [ ] **Step 3: Run type checks on touched Python files**

Run:

```bash
rtk uv run mypy \
  adapters/http/app.py \
  adapters/http/session_runner.py \
  adapters/http/sse_routes.py \
  adapters/examiner/llm_examiner.py \
  adapters/scorer/llm_experiment_design_scorer.py \
  core/events.py \
  core/projections.py \
  core/problem_bank.py \
  core/coverage.py \
  tests/integration/test_chat_turn_submission_guards.py \
  tests/integration/test_chat_advance_idempotency.py \
  tests/integration/test_problem_plan_persistence.py \
  tests/integration/test_probe_persistence.py \
  tests/integration/test_problem_coverage_flow.py \
  tests/unit/test_examiner.py \
  tests/unit/test_scorers.py
```

Expected: pass.

- [ ] **Step 4: Run full suite**

Run:

```bash
rtk uv run pytest -q
```

Expected: pass.

- [ ] **Step 5: Review final diff for minimality**

Run:

```bash
rtk git diff --stat
rtk git diff -- adapters/http/app.py adapters/http/session_runner.py core/events.py core/projections.py
```

Expected: diff touches only the files listed in this plan and implements the RCA fixes without unrelated refactors.

---

## Implementation Order

1. Candidate submit guards and retry recovery.
2. System advancement idempotency.
3. Durable problem-plan selection.
4. Probe persistence and fallback behavior.
5. Problem coverage observations and examiner context.
6. Chat rubric/scorer alignment.
7. Documentation and full verification.

This order reduces risk: first protect the event log from invalid writes, then make system writes idempotent, then fix interview semantics and scoring completeness.
