# Latency, Profile Alignment, and Scoring Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep all candidate-facing chat steps within 1-3 seconds while making problem selection profile-aware and scorer outputs schema-safe.

**Architecture:** Preserve the event-sourced backbone (`Session -> Turn -> Artifact -> Signal -> Score`). Move expensive scoring off the synchronous chat advance path, make problem planning deterministic but profile-ranked, and enforce provider-native JSON schema for scorer/examiner outputs.

**Tech Stack:** Python 3.12, FastAPI/TestClient, SQLite event log, Pydantic domain models, OpenAI Chat Completions via `urllib`, pytest/ruff/mypy.

---

## Parallelization Map

```text
Wave 1 can run in parallel:
  Task 1A Profile-aware problem selection
  Task 1B OpenAI structured-output transport
  Task 1C Scoring event contracts and projection states

Wave 2 depends on Wave 1B/1C:
  Task 2A Problem-level combined scorer
  Task 2B Background scoring worker

Wave 3 depends on Wave 2:
  Task 3A SessionRunner async scoring integration
  Task 3B Result/progress UI
  Task 3C Observability and latency tests
```

Ownership rule for parallel workers: workers are not alone in the codebase. Do not revert edits made by others. Keep write sets disjoint unless a task explicitly depends on a prior task.

---

## File Map

### Create
- `core/problem_selection.py` — deterministic profile-aware problem ranking.
- `tests/unit/test_problem_selection.py` — selector tests.
- `adapters/llm/structured_schema.py` — OpenAI JSON schema payload builders and schema constants.
- `tests/unit/test_openai_structured_outputs.py` — request-payload and schema tests.
- `adapters/scorer/problem_scorer.py` — one-call-per-problem scorer.
- `tests/unit/test_problem_scorer.py` — problem scorer parser/fallback tests.
- `adapters/scorer/background_worker.py` — in-process background scoring queue/worker.
- `tests/unit/test_background_scoring.py` — worker behavior tests.
- `tests/integration/test_real_time_chat_budget_async_scoring.py` — chat latency regression with slow fake scorer.

### Modify
- `core/events.py` — add `ScoringRequested`, `ScoringCompleted`, optional `ProblemPlanSelected.selection_rationale`.
- `core/projections.py` — project scoring pending/completed state.
- `adapters/http/session_runner.py` — select profile-ranked plan and request background scoring on `ProblemClosed`.
- `adapters/http/app.py` — wire background scorer, expose scoring pending on result page.
- `adapters/http/templates/result.html` — show pending/partial/final scoring states.
- `adapters/llm/openai_router.py` — support `response_format=json_schema`, `max_completion_tokens`.
- `adapters/llm/router.py` — pass structured schema through `call_json` without free-form extraction when available.
- `adapters/scorer/_base.py` — reuse strict schema parsing/clamping rules or delegate to problem scorer.
- `tests/unit/test_events.py`, `tests/unit/test_openai_router.py`, relevant integration tests.

---

## Task 1A: Profile-Aware Problem Selection

**Parallel:** Can run independently of Tasks 1B and 1C.

**Files:**
- Create: `core/problem_selection.py`
- Modify: `adapters/http/session_runner.py`
- Modify: `core/events.py`
- Test: `tests/unit/test_problem_selection.py`

- [ ] **Step 1: Write selector tests**

Create `tests/unit/test_problem_selection.py` with cases proving:
- profile skills/tags rank matching problems first;
- dimension coverage still prevents a one-topic interview;
- same session/profile/problem bank yields deterministic ordering;
- empty profile falls back to current balanced deterministic behavior.

Expected command:

```bash
rtk uv run pytest -q tests/unit/test_problem_selection.py
```

Expected before implementation: import failure for `core.problem_selection`.

- [ ] **Step 2: Implement `ProfileAwareProblemSelector`**

Create `core/problem_selection.py` with:
- `ProfileFeatures` dataclass: role text, skills, claims text;
- tokenizer for lowercase alphanumeric terms;
- tag/skill overlap score;
- role/context/opener overlap score;
- dimension coverage bonus;
- deterministic hash tie-breaker;
- `select(session_id, profile, entries, count=4) -> ProblemSelection`.

Do not call an LLM in the selector.

- [ ] **Step 3: Persist selection rationale**

Modify `core/events.py` `ProblemPlanSelected` to include:

```python
selection_rationale: dict[str, tuple[str, ...]] = Field(default_factory=dict)
```

Each selected problem should record matched terms/tags, e.g. `("pricing", "ab-test")`.

- [ ] **Step 4: Wire selector into `SessionRunner._planned_problems`**

When `problem_bank` exists and no plan has been selected:
- load `USER.md` via `_load_user_context(session_id)`;
- build profile features from text;
- use selector over `problem_bank.entries` or add a safe accessor for entries;
- append `ProblemPlanSelected(source="profile_aware_selector", selection_rationale=...)`.

Keep the existing session-id deterministic fallback for missing profile.

- [ ] **Step 5: Verify**

Run:

```bash
rtk uv run pytest -q tests/unit/test_problem_selection.py tests/integration/test_problem_bank_session.py tests/integration/test_problem_plan_persistence.py
rtk uv run ruff check core/problem_selection.py tests/unit/test_problem_selection.py
```

Expected: all pass.

---

## Task 1B: OpenAI Structured Outputs Support

**Parallel:** Can run independently of Tasks 1A and 1C.

**Files:**
- Create: `adapters/llm/structured_schema.py`
- Modify: `adapters/llm/openai_router.py`
- Modify: `adapters/llm/router.py`
- Test: `tests/unit/test_openai_structured_outputs.py`
- Test: `tests/unit/test_openai_router.py`

- [ ] **Step 1: Write request payload tests**

Add tests asserting that structured calls include:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "scoring_result",
      "strict": true,
      "schema": {"type": "object"}
    }
  },
  "max_completion_tokens": 500
}
```

Also assert legacy unstructured calls still work.

- [ ] **Step 2: Add schema builder module**

Create `adapters/llm/structured_schema.py` with:
- `JsonSchemaRequest` dataclass;
- `scoring_result_schema(dimensions: tuple[str, ...])`;
- `examiner_outcome_schema()`;
- helper `response_format(name, schema)`.

Scorer signal schema must enforce numeric `value` and `confidence` between 0 and 1.

- [ ] **Step 3: Extend router/provider protocol**

Modify `OpenAIRouter.call(...)` and `ModelRouter.call_json(...)` to accept optional:

```python
json_schema: dict[str, object] | None = None
schema_name: str | None = None
max_completion_tokens: int | None = None
```

When `json_schema` is provided, `OpenAIRouter._build_request` must send provider-native `response_format` and should not rely on prompt-only JSON instructions.

- [ ] **Step 4: Preserve reasoning-effort gating**

Ensure `reasoning_effort` is still only sent for supported reasoning model prefixes.

- [ ] **Step 5: Verify**

Run:

```bash
rtk uv run pytest -q tests/unit/test_openai_router.py tests/unit/test_openai_structured_outputs.py tests/unit/test_llm_factory.py
rtk uv run ruff check adapters/llm/openai_router.py adapters/llm/router.py adapters/llm/structured_schema.py tests/unit/test_openai_structured_outputs.py
```

Expected: all pass.

---

## Task 1C: Scoring Request Event Contracts and Projection State

**Parallel:** Can run independently of Tasks 1A and 1B.

**Files:**
- Modify: `core/events.py`
- Modify: `core/projections.py`
- Test: `tests/unit/test_events.py`
- Test: `tests/unit/test_scoring_projection.py`

- [ ] **Step 1: Write projection tests**

Create/update tests proving:
- `ScoringRequested(problem_id, artifact_ids, dimensions)` marks a problem as pending;
- `ScoringCompleted(problem_id)` marks it complete;
- repeated idempotent events do not duplicate pending state;
- existing `SignalEmitted` and `ScoreComputed` behavior remains unchanged.

- [ ] **Step 2: Add events**

Add Pydantic events:

```python
class ScoringRequested(_Evt):
    problem_id: ProblemId
    artifact_ids: tuple[str, ...]
    dimensions: tuple[Dimension, ...]

class ScoringCompleted(_Evt):
    problem_id: ProblemId
```

- [ ] **Step 3: Project scoring state**

Extend `ScoreStore` or add a focused projection structure to track:

```python
pending_problem_ids: set[ProblemId]
completed_problem_ids: set[ProblemId]
```

Expose read methods used by result templates:
- `is_scoring_pending(session_id)`;
- `completed_problem_ids(session_id)`.

- [ ] **Step 4: Verify**

Run:

```bash
rtk uv run pytest -q tests/unit/test_events.py tests/unit/test_scoring_projection.py
rtk uv run mypy core/events.py core/projections.py tests/unit/test_scoring_projection.py
```

Expected: all pass.

---

## Task 2A: Problem-Level Combined Scorer

**Parallel:** Starts after Task 1B schema support. Can run in parallel with Task 2B once event contracts exist.

**Files:**
- Create: `adapters/scorer/problem_scorer.py`
- Modify: `adapters/scorer/__init__.py`
- Test: `tests/unit/test_problem_scorer.py`

- [ ] **Step 1: Write scorer tests**

Tests must cover:
- one LLM call scores multiple dimensions for one problem transcript;
- malformed JSON becomes `ScorerFailed` plus bounded heuristic signals;
- string values like `"medium"` are rejected, not silently accepted;
- numeric values above 1 are rejected and replaced by heuristic fallback;
- all emitted signals have `source_refs` set to candidate artifact ids.

- [ ] **Step 2: Implement problem transcript input**

`ProblemLlmScorer.score_problem(...)` should accept:

```python
session_id: str
problem_id: ProblemId
problem_context: str
candidate_artifacts: tuple[Artifact, ...]
dimensions: tuple[Dimension, ...]
```

Return:

```python
ProblemScoringResult(signals=tuple[Signal, ...], failures=tuple[ScorerFailed, ...])
```

- [ ] **Step 3: Use strict structured schema**

Call `ModelRouter.call_json(..., json_schema=scoring_result_schema(...), schema_name="problem_scoring_result", max_completion_tokens=700)`.

- [ ] **Step 4: Add deterministic fallback**

For failed dimensions, emit heuristic signals using existing keyword heuristics or a simple shared bounded fallback:
- value in `[0, 1]`;
- confidence `0.2`;
- emitted_by `problem_scorer:heuristic`.

- [ ] **Step 5: Verify**

Run:

```bash
rtk uv run pytest -q tests/unit/test_problem_scorer.py tests/unit/test_openai_structured_outputs.py
rtk uv run ruff check adapters/scorer/problem_scorer.py tests/unit/test_problem_scorer.py
```

Expected: all pass.

---

## Task 2B: Background Scoring Worker

**Parallel:** Starts after Task 1C. Can run in parallel with Task 2A if using a fake scorer protocol first.

**Files:**
- Create: `adapters/scorer/background_worker.py`
- Modify: `adapters/http/app.py`
- Test: `tests/unit/test_background_scoring.py`

- [ ] **Step 1: Write worker tests**

Tests must prove:
- enqueue returns immediately;
- worker emits `SignalEmitted` and `ScoringCompleted`;
- worker emits `ScorerFailed` and `ScoringCompleted` on scorer failure;
- shutdown drains or safely stops without corrupting the event log.

- [ ] **Step 2: Implement in-process worker**

Implement a minimal thread-backed queue:

```python
class BackgroundScoringWorker:
    def enqueue(self, job: ScoringJob) -> None: ...
    def start(self) -> None: ...
    def stop(self, timeout: float = 2.0) -> None: ...
```

This is MVP-safe; later production can swap to Celery/RQ without changing domain events.

- [ ] **Step 3: Wire lifecycle in FastAPI**

In `make_app`, accept optional `scoring_worker`. Attach it to `app.state.scoring_worker`. Start/stop in app lifespan only when provided.

- [ ] **Step 4: Verify**

Run:

```bash
rtk uv run pytest -q tests/unit/test_background_scoring.py tests/integration/test_http_happy.py
rtk uv run mypy adapters/scorer/background_worker.py tests/unit/test_background_scoring.py
```

Expected: all pass.

---

## Task 3A: SessionRunner Async Scoring Integration

**Parallel:** Starts after Tasks 1C, 2A, and 2B. Can run alongside Task 3B after shared projection methods exist.

**Files:**
- Modify: `adapters/http/session_runner.py`
- Modify: `adapters/http/app.py`
- Test: `tests/integration/test_real_time_chat_budget_async_scoring.py`
- Test: `tests/integration/test_problem_coverage_flow.py`

- [ ] **Step 1: Write latency regression test**

Create a fake slow scorer taking 5 seconds. Prove:
- closing a problem enqueues scoring and returns within 3 seconds;
- `GET /sessions/{id}` does not block on slow scorer;
- score events arrive after worker finishes.

- [ ] **Step 2: Emit `ScoringRequested` on problem close**

In `_do_close_problem`, collect candidate artifacts for that problem and append:

```python
ScoringRequested(problem_id=..., artifact_ids=..., dimensions=...)
```

Use idempotency key:

```text
scoring-requested:{problem_id}
```

- [ ] **Step 3: Enqueue background job**

If `runner` has a worker reference or if `app` coordinates jobs after request, enqueue a `ScoringJob` for the closed problem. Keep event append idempotent so retries do not duplicate jobs.

- [ ] **Step 4: Stop synchronous full scoring from blocking chat**

Update `advance()` so candidate-facing GET returns after problem close/next introduction instead of performing all `RequestScoring` actions inline. Preserve a compatibility path for tests that construct `SessionRunner` without a worker.

- [ ] **Step 5: Verify**

Run:

```bash
rtk uv run pytest -q tests/integration/test_real_time_chat_budget_async_scoring.py tests/integration/test_problem_coverage_flow.py tests/integration/test_http_happy.py
```

Expected: all pass, with latency assertion below 3 seconds.

---

## Task 3B: Result Page Pending/Partial/Final Score UX

**Parallel:** Starts after Task 1C. Can run alongside Task 3A.

**Files:**
- Modify: `adapters/http/app.py`
- Modify: `adapters/http/templates/result.html`
- Test: `tests/integration/test_result_scoring_progress.py`

- [ ] **Step 1: Write result-page tests**

Tests must cover:
- no final score but pending scoring -> page shows `Scoring in progress`;
- some per-problem scores -> page shows partial score section;
- final `ScoreComputed` -> page shows final composite score.

- [ ] **Step 2: Pass scoring state into template**

In `get_result`, include:

```python
"scoring_pending": scores.is_scoring_pending(session_id),
"completed_problem_ids": scores.completed_problem_ids(session_id),
```

- [ ] **Step 3: Update template**

Render explicit states:
- pending badge;
- partial problem scores if available;
- final score when `ScoreComputed` exists.

- [ ] **Step 4: Verify**

Run:

```bash
rtk uv run pytest -q tests/integration/test_result_scoring_progress.py tests/integration/test_recruiter_evidence_view.py
```

Expected: all pass.

---

## Task 3C: Observability and Latency Budgets

**Parallel:** Starts after Task 3A but individual metrics additions can begin earlier.

**Files:**
- Modify: `core/metrics.py` or existing metrics helper if present
- Modify: `adapters/http/app.py`
- Modify: `tools/latency_probe.py`
- Test: `tests/integration/test_internal_metrics.py`
- Test: `tests/integration/test_chat_latency_budget.py`

- [ ] **Step 1: Add metrics tests**

Assert these metrics exist after a session run:
- `chat_request_ms` by route;
- `llm_call_ms` by component;
- `scoring_queue_depth`;
- `scoring_job_ms`;
- `structured_output_failures_total`.

- [ ] **Step 2: Instrument LLM calls**

Add timing labels for:
- challenger;
- examiner;
- problem scorer;
- fallback/parse failure.

Do not log raw prompts or candidate text.

- [ ] **Step 3: Update latency probe tool**

`tools/latency_probe.py` should report:
- POST submit latency;
- examiner render latency;
- next-problem render latency;
- result shell latency;
- background scoring duration separately.

- [ ] **Step 4: Verify full gates**

Run:

```bash
rtk uv run pytest -q tests/integration/test_internal_metrics.py tests/integration/test_chat_latency_budget.py tests/integration/test_real_time_chat_budget_async_scoring.py
rtk uv run ruff check core adapters tools tests
rtk uv run mypy core adapters tests/unit/test_problem_selection.py tests/unit/test_problem_scorer.py tests/unit/test_background_scoring.py
rtk git diff --check
```

Expected: all pass.

---

## Acceptance Criteria

- Profile-aware problem plan prioritizes candidate-matching problem tags while preserving dimension coverage and deterministic repeatability.
- Candidate-facing POST answer remains below 100ms in fake latency tests.
- Candidate-facing GET for examiner/next problem remains below 3s under simulated slow scoring.
- Full scoring no longer blocks chat advancement or result shell render.
- Problem-level scoring reduces call count from `artifacts × dimensions` to `closed problems × 1`.
- Scorer output parser never accepts string scores or out-of-range numeric values.
- OpenAI structured-output requests use strict JSON schema for scorer and examiner paths.
- Result page clearly distinguishes pending, partial, and final scoring.
- No raw prompt, resume, or candidate text is logged in metrics.
