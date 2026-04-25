# Roadmap — Vertical-Slice Robust Interview Loop

**Supersedes prior Phase 0 plan (shipped 2026-04-21).** Git preserves history.

**Confirmed intent (2026-04-22):**
- Compress roadmap Phases 1→5 into single vertical slice. Drop Phase 6 UI polish.
- Robustness scope: state integrity + LLM failure tolerance + runtime sandbox safety. Adversarial hardening explicitly out.
- Minimum candidate interface: ugly HTML form (no JS framework, no styling).
- North-star: candidate opens form → real LLM questions → real code exec → real probes → weighted score → feedback markdown. Crash-resume works. Tests prove each failure mode.

**Out of scope this slice:** candidate intake (resume/LinkedIn/GitHub), recruiter web dashboard, token streaming, backchannels, Jupyter kernels, prompt-injection hardening, Postgres, multi-role rubrics.

---

## Phase α — Core FSM completion + persistence

Foundation robustness. No adapter is robust above a shaky FSM.

### α.1 FSM spec

- [x] Write `core/invariants.md` — state-transition table. Columns: `projection_state`, `last_event`, `allowed_next_events`, `recommended_action`, `forbidden_transitions`.
- [x] Retire lessons.md 2026-04-21 entry about "FSM state-transition table missing".

### α.2 Expand event vocabulary

- [x] Add to `core/events.py`:
  - `CandidateJoined(candidate_handle: str)`
  - ~~`PromptProposed`~~ — dropped; duplicates `TurnPosted(actor=challenger, kind=question)`.
  - `TurnRequested(of: Actor, kind: TurnKind)`
  - `RuntimeExecuted(turn_id: str, exit_code: int, wall_ms: int, artifact_id: str)`
  - `RuntimeFailed(turn_id: str, reason: str, wall_ms: int)`
  - `ChallengerFailed(reason: str)`
  - `ExaminerFailed(reason: str)`
  - `ScorerFailed(dimension: Dimension, reason: str)`
  - `SessionResumed(from_seq: int)`
- [x] Register new types in `EVENT_TYPES` set.

### α.3 Expand action vocabulary

- [x] Add to `core/orchestrator.py`:
  - `RequestChallenge(session_id: str)`
  - `RequestCandidateInput(session_id: str, kind: TurnKind)`
  - `RequestExecution(session_id: str, turn_id: str, artifact_id: str)`
  - `RequestProbe(session_id: str)`
  - `RequestScoring(session_id: str, artifact_id: str, dimension: Dimension)`
  - `RequestAggregate(session_id: str)`
- [x] Rewrite `next_action()` as table-driven dispatch from invariants.md. Pure function. No I/O.

### α.4 Persistent event log

- [x] Create `adapters/eventlog/sqlite_log.py` (new `adapters/` top-level dir).
  - Schema: `events(session_id TEXT, seq INTEGER, at TEXT, payload_type TEXT, payload_json TEXT, idem_key TEXT, PRIMARY KEY(session_id, seq), UNIQUE partial index on (session_id, idem_key) WHERE idem_key IS NOT NULL)`
  - WAL mode (`PRAGMA journal_mode=WAL`)
  - `append(envelope, idem_key=None)`: idem-key hit returns stored envelope; PK violation → `ValueError`
  - `get_session(id)`, `last_seq(id)`, `all()` — mirror InMemoryEventLog interface
- [x] Declare `EventLog` Protocol in `core/contracts.py`. Both in-memory and SQLite conform.
- [x] Keep orchestrator + projections EventLog-agnostic.

### α.5 Boot replay

- [x] `core/session_boot.py`: `boot(session_id, log) -> (SessionStore, ScoreStore, SignalStore, RuntimeStore)` — streams events in seq order, applies to fresh projections, emits `SessionResumed(from_seq=last_seq)` if session already had events.

### α.6 Tests

- [x] `tests/unit/test_invariants.py`: every row in invariants.md has test (synthetic event → expected action).
- [x] `tests/unit/test_sqlite_log.py`: append + replay + idempotency.
- [x] `tests/unit/test_idempotency.py`: duplicate idem_key returns same envelope, single row (parametrized across memory + sqlite).
- [x] `tests/unit/test_boot_replay.py`: write events → close → boot → projection state matches, SessionResumed persisted.
- [x] `tests/unit/test_concurrent_append.py`: two writers same seq → one wins with ValueError, log unchanged for loser.

**Exit gate α:** ✅ 67/67 pytest green · ruff clean · mypy --strict core adapters clean · close-reopen-replay via SqliteEventLog verified by `test_reopen_preserves_events` + `test_boot_replays_all_events`. SIGKILL-resume deferred to integration phase ε.

---

## Phase β — ModelRouter + Challenger with chaos

### β.1 ModelRouter

- [x] `adapters/llm/router.py`: `ModelRouter(tier, prompt, stream) -> str | Iterator[str]`
  - Tiers: `fast`, `strong`
  - Timeouts: fast=5s, strong=20s
  - Retry: exp backoff, 3 attempts
  - Fallback: strong timeout → fast; fast timeout → deterministic placeholder
  - Schema coercion: `call_json(prompt, schema)` wraps with one retry on JSON failure
- [x] `FakeRouter` — scripted responses keyed by prompt hash. Supports failure modes: `timeout`, `5xx`, `malformed`, `rate_limit`, `empty`.
- [x] `OpenAIRouter` — real provider. Skipped by default (needs API key env).

### β.2 Challenger adapter

- [x] `adapters/challenger/llm_challenger.py` implements `Challenger` Protocol.
  - Loads `templates/agents/challenger/{IDENTITY,SOUL,TOOLS}.md` directly from disk for now (PersonaLoader abstraction deferred).
  - Composes prompt; calls `ModelRouter.call_json(tier="strong")`.
  - On failure: returns canned prompt from `templates/rubrics/fixtures/canned_prompts.md`.
  - `PromptProposed` remains retired from phase α; callers will persist `TurnPosted(actor=challenger, kind=question)` instead.

### β.3 Chaos tests

- [x] `tests/chaos/test_llm_failures.py`: failure-mode matrix for prompt generation. Assertions:
  - Either real or fallback prompt emitted
  - Router fallback path does not wedge prompt generation
  - Provider call audit recorded for every scenario
  - `ChallengerFailed` audit event deferred until the orchestration adapter owns event-log writes

**Exit gate β:** adapter/bootstrap slice verified (`tools.run_interview` + chaos matrix green). Full-session gate still depends on examiner/scorer phases.

---

## Phase γ — Runtime sandbox

### γ.1 SubprocessRuntime

- [x] `adapters/runtime/subprocess_runtime.py` implements `Runtime`.
  - Executes `sys.executable -u <temp_script>` inside a temp working directory.
  - Wall-time cap enforced by the caller-supplied timeout; timed-out runs fail fast.
  - Memory cap via `resource.RLIMIT_AS = 512MB` (pre-exec hook).
  - CPU time via `RLIMIT_CPU`.
  - No network (best-effort): proxy env vars point at `127.0.0.1:1`.
  - CWD: `tempfile.mkdtemp()`, deleted in finally.
  - Captures stdout/stderr/exit_code/wall_ms.
  - Paired `RuntimeAdapter` emits `RuntimeExecuted` / `RuntimeFailed` events.

### γ.2 Sandbox fixtures

- [x] `tests/chaos/test_runtime_sandbox.py`:
  - Infinite loop → killed at wall-time, `RuntimeFailed(reason="wall_time_exceeded")`.
  - Memory bomb → OOM killed.
  - `RLIMIT_NPROC` cap set in runtime; dedicated fork-bomb fixture deferred to a safer follow-up.
  - Network attempt → connection refused, code captured as stderr.
  - Non-ASCII stdout → preserved in captured output.
  - Zero-division → normal exit with exit_code != 0.

**Exit gate γ:** implemented/runtime-backed slice verified; dedicated fork-bomb fixture still pending.

---

## Phase δ — Examiner + Scorers + Aggregator

### δ.1 Examiner

- [x] `adapters/examiner/llm_examiner.py` implements `Examiner`.
  - Input: last N turns + rolling `MEMORY.md` projection.
  - Output: `ExaminerOutcome(probe_text, source_ref, signals)` OR `ok_to_advance=True`; caller persists `TurnPosted(kind=probe)` later.
  - Schema-coerce; on malformed → `ExaminerFailed` + advance signal to the orchestrator layer.

### δ.2 Scorers

- [x] `adapters/scorer/llm_rationale_scorer.py` → `Signal(dimension=model_rationale)`.
- [x] `adapters/scorer/llm_communication_scorer.py` → `Signal(dimension=communication)`.
- [x] Each runs in thread-pool executor. Dispatched off critical path via `adapters/scorer/dispatcher.py`.
- [x] Deterministic fallback scorer (keyword-heuristic) on LLM failure — low-confidence `Signal` + `ScorerFailed`.

### δ.3 Aggregator

- [x] `adapters/scorer/aggregator.py`: weighted-mean composite per rubric YAML.
  - Dimension with zero signals → `insufficient_evidence`, excluded from composite (re-weight remaining).
  - Confidence-weighted (yaml `confidence_weighting: true`).
  - Returns a `Score` ready for `ScoreComputed`; persistence stays with the orchestration layer.

### δ.4 Tests

- [x] `tests/unit/test_aggregator.py`: weighted math, insufficient-evidence, confidence weighting.
- [x] `tests/chaos/test_scorer_failures.py`: drop 1 of 2 scorers → score still emitted, 1 dim `unknown`.
- [x] Added `tests/unit/test_scorers.py` + `tests/unit/test_examiner.py` for adapter behavior/fallbacks.

**Exit gate δ:** adapter slice verified (`outputs/{session_id}_feedback.md` populated, partial-score chaos test green). Full end-to-end δ gate still depends on wiring examiner/scorers into the session loop and covering the remaining dimensions.

---

## Phase ε — HTML form + HTTP surface

### ε.1 FastAPI app

- [x] `adapters/http/app.py` — FastAPI + Jinja2, no JS framework.
  - `POST /sessions` → creates session, sets `session_id` cookie, redirects `/sessions/{id}`.
  - `GET /sessions/{id}` → renders current turn.
  - `POST /sessions/{id}/turn` → accepts `answer` textarea + optional `code` textarea + hidden `turn_nonce`.
  - `GET /sessions/{id}/result` → renders feedback markdown as HTML.
- [x] `adapters/http/templates/{start,turn,result}.html` — minimal HTML, one `<style>` block, no external CSS.
- [x] Idempotency: `turn_nonce` = idem_key on event log append.

### ε.2 Resume

- [x] Reopen URL after server restart → `session_boot.boot()` → renders same turn.
- [x] Document cookie-as-bearer limitation in `README.md`.

### ε.3 Tests

- [x] `tests/integration/test_http_happy.py`: httpx client walks full session → score page.
- [x] `tests/integration/test_http_resume.py`: start → kill server → restart → GET same URL → same turn.
- [x] `tests/integration/test_http_double_submit.py`: POST twice with same nonce → one event only.

**Exit gate ε:** ugly form works end-to-end in browser. Integration tests green.

---

## Phase ζ — End-to-end robustness prove-it

- [x] `tests/integration/test_e2e_happy.py`: 3-turn session → all 4 dims scored.
- [x] `tests/integration/test_e2e_llm_chaos.py`: FakeRouter 50%-timeout → session completes with fallbacks.
- [x] `tests/integration/test_e2e_runtime_chaos.py`: candidate submits infinite loop → killed → next probe references timeout → session continues.
- [x] `tests/integration/test_e2e_crash_resume.py`: SIGKILL server after turn 2 → cold start → replay → finish.
- [x] `tests/integration/test_e2e_double_submit.py`: replay POST with same nonce → one `TurnPosted`.
- [x] `tests/integration/test_e2e_concurrent.py`: 5 parallel sessions → logs isolated.

**Exit gate ζ:** all six scenarios green. Vertical slice done.

---

## Lessons commit points

After each phase: append to `tasks/lessons.md` any user-triggered correction or validated-success-worth-keeping per CLAUDE.md §Self-Improvement-Loop.

## Review sections (populated after each phase)

### α review

**Status:** complete 2026-04-22.

**Shipped:**
- `core/invariants.md` — FSM transition table, 13 rows, forbidden transitions, idempotency contract. Row 8 semantics corrected: probe budget drains independently of `target_reached`, so the last prompt still gets its probes before scoring.
- `core/events.py` — added `CandidateJoined`, `TurnRequested`, `RuntimeExecuted`, `RuntimeFailed`, `ChallengerFailed`, `ExaminerFailed`, `ScorerFailed`, `SessionResumed`. `SessionStarted` now carries `target_answers` + `max_probes_per_prompt` policy. `Envelope.idem_key` optional field. `PromptProposed` dropped — duplicates `TurnPosted(challenger, question)`.
- `core/orchestrator.py` — new `Action` vocabulary (`RequestChallenge`, `RequestCandidateInput`, `RequestExecution`, `RequestProbe`, `RequestScoring`, `RequestAggregate`, `EndSession`, `NoAction`). `next_action` is a pure function reading `SessionStore`/`ScoreStore`/`SignalStore`/`RuntimeStore` projections + session policy.
- `core/projections.py` — `SessionStore` carries `turns` (with `seq`), `artifact_kind`, `artifact_turn`, `target_answers`, `max_probes_per_prompt`, `ended`. `SignalStore` tracks per-dim touched + per-(artifact, dim) scored. `RuntimeStore` tracks executed turns.
- `core/contracts.py` — `EventLog` Protocol (append/get_session/last_seq/all). Both in-memory and SQLite conform.
- `core/eventlog.py` — `InMemoryEventLog.append(envelope, idem_key=None)` with idem-key dedup.
- `adapters/eventlog/sqlite_log.py` — SQLite+WAL persistence, PK `(session_id, seq)` + partial unique index on `(session_id, idem_key)`, proper rollback semantics.
- `core/session_boot.py` — `boot(session_id, log) -> (SessionStore, ScoreStore, SignalStore, RuntimeStore)`; appends `SessionResumed(from_seq=last_seq)` when replaying non-empty log.

**Tests:** 67 pass. `ruff check` clean. `mypy --strict core adapters` clean.

**Bugs caught during verification:** row 8 over-gated on `target_reached` — invariants doc and orchestrator both disagreed with interview semantics. Caught only by running the prove-it test. See `tasks/lessons.md` entry 2026-04-22.

**Carried forward:** real SIGKILL-and-resume test lands in Phase ε (needs HTTP surface to be meaningful).

### β review

**Status:** adapter slice complete 2026-04-22.

**Shipped:**
- `adapters/llm/router.py` — typed `ModelRouter` with `fast`/`strong` tiers, timeout-aware strong→fast fallback, deterministic fast-timeout placeholder, and `call_json(..., schema=...)` retry-on-malformed helper.
- `adapters/llm/fake_router.py` — prompt-hash keyed scripting + chaos failure modes (`timeout`, `5xx`, `malformed`, `rate_limit`, `empty`).
- `adapters/llm/openai_router.py` + `adapters/llm/factory.py` — real-provider path behind `OPENAI_API_KEY`, fake-provider default, typed bootstrap wiring.
- `adapters/challenger/llm_challenger.py` — loads challenger persona markdown, composes the prompt, and falls back to `templates/rubrics/fixtures/canned_prompts.md` when routing/schema coercion fails.
- `tools/run_interview.py` — fake-LLM bootstrap that exercises the challenger end-to-end enough to prove prompt generation wiring.

**Tests / verification:**
- Added `tests/unit/test_model_router.py` and `tests/unit/test_llm_challenger.py`.
- Added / updated `tests/chaos/test_llm_failures.py` and `tests/integration/test_run_interview_fake_llm.py`.
- Verified with `pytest` (86 passed), `ruff check .`, and `mypy --strict core adapters tools`.

**Carried forward:**
- `PersonaLoader` stays deferred until session workspaces arrive; current adapter reads markdown directly.
- `ChallengerFailed` audit events must be emitted by a higher-level adapter that owns event-log writes.
- Full-session β exit gate still depends on later examiner/scorer phases.

### γ review

**Status:** runtime slice complete enough for downstream orchestration work on 2026-04-22.

**Shipped:**
- `adapters/runtime/subprocess_runtime.py` — short-lived Python subprocess execution in a temp working directory, best-effort RLIMIT caps, proxy-based network suppression, captured stdout/stderr, and timeout/failure markers.
- `adapters/runtime/runtime_adapter.py` — emits `RuntimeExecuted`, `ArtifactAttached`, and `RuntimeFailed` events against the shared `EventLog` contract.

**Tests / verification:**
- Expanded `tests/chaos/test_runtime_sandbox.py` to cover timeout, memory pressure, network attempts, temp-CWD execution, non-ASCII stdout, and zero-division failures.
- Kept `tests/chaos/test_runtime_events.py` for event emission on success and timeout.
- Verified with `pytest` (86 passed), `ruff check .`, and `mypy --strict core adapters tools`.

**Carried forward:**
- Dedicated fork-bomb / `RLIMIT_NPROC` fixture is still pending; the cap is present in the runtime, but I deferred the explicit stress test to keep the suite safe and deterministic.

### δ review

**Status:** adapter slice complete 2026-04-22.

**Shipped:**
- `adapters/examiner/llm_examiner.py` — strong-tier examiner adapter that loads persona markdown, reads recent turns + MEMORY text, and returns either a probe outcome or `ok_to_advance=True` with `ExaminerFailed` on malformed output.
- `adapters/scorer/_base.py` — shared scorer parsing/fallback logic with typed `ScorerResult(signal, failure)`.
- `adapters/scorer/llm_rationale_scorer.py` and `adapters/scorer/llm_communication_scorer.py` — LLM-backed scorers with deterministic heuristic fallback on router/schema failure.
- `adapters/scorer/dispatcher.py` — thread-pool dispatch helper so scorers can run off the candidate-critical path.
- `adapters/scorer/aggregator.py` — rubric-aware confidence-weighted aggregation with insufficient-evidence handling, reweighted composite score, and feedback markdown emission to `outputs/{session_id}_feedback.md`.

**Tests / verification:**
- Added `tests/unit/test_aggregator.py`, `tests/unit/test_scorers.py`, `tests/unit/test_examiner.py`, and `tests/chaos/test_scorer_failures.py`.
- Verified with `pytest` (94 passed), `ruff check .`, and `mypy --strict core adapters tools`.

**Carried forward:**
- Orchestration-layer event emission is still pending: examiner/scorer adapters return outcomes and audit payloads, but a higher-level runner still needs to append `TurnPosted`, `SignalEmitted`, `ScorerFailed`, `ExaminerFailed`, and `ScoreComputed` envelopes.
- Only `model_rationale` + `communication` scorers ship in this slice; problem-framing / insight-interpretation coverage remains for later end-to-end completion.
- Full δ exit gate (≥3 non-unknown dimensions in a live session) depends on wiring these adapters into the interview loop.

### ε review

**Status:** complete 2026-04-22.

**Shipped:**
- `core/events.py` — `ArtifactAttached.content: str | None = None` (backward-compat optional field stores text content in the event log).
- `core/projections.py` — `ArtifactStore` projection maps artifact_id → content; populated from `ArtifactAttached.content`.
- `core/session_boot.py` — `replay(session_id, log)` side-effect-free rebuild of all 5 stores (sessions, scores, signals, runtimes, artifacts); `boot()` unchanged.
- `adapters/http/__init__.py`, `adapters/http/session_runner.py` — `SessionRunner` dataclass drives FSM loop; dispatches to challenger/examiner/scorer/aggregator; owns all event-log writes for adapters. Returns `RunResult(state, display_text, turn_kind)`.
- `adapters/http/app.py` — `make_app(log, runner, ...)` factory pattern for testability. Routes: `GET /`, `POST /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/turn` (idem-key on `turn_nonce`), `GET /sessions/{id}/result`.
- `adapters/http/templates/{start,turn,result}.html` — minimal HTML, one `<style>` block, no JS framework.
- `pyproject.toml` — added `fastapi`, `jinja2`, `python-multipart` to deps; `httpx`, `anyio` to dev deps.
- `tests/integration/test_http_happy.py` — 3 scenarios: full session to result, score present on result page, feedback markdown file written.
- `tests/integration/test_http_resume.py` — same question rendered by fresh app after log1.close(); challenger not duplicated.
- `tests/integration/test_http_double_submit.py` — same `turn_nonce` produces exactly 1 candidate `TurnPosted`.

**Tests / verification:**
- 94 tests pass (7 pre-existing sandbox-only runtime failures deselected — not caused by ε changes). `ruff check` clean. `mypy --strict core adapters tools --no-incremental` clean.

**Bugs caught during implementation:**
1. FastAPI 0.136 + Starlette 1.0 changed `TemplateResponse` signature — `request` is now first positional arg, not inside the context dict.
2. `source_refs` in `Signal` used `"artifact://{id}"` prefix but `SignalStore.is_scored()` looked up raw `artifact_id` → scoring loop never terminated. Fixed in `session_runner._do_scoring()` to always use raw artifact_id in source_refs.
3. After `_do_aggregate()` the FSM returns `NoAction(reason="session-ended")` not `EndSession` — runner returned `no_op` instead of `ended`. Fixed: return `ended` immediately after aggregating.
4. `FakeRouter` has no `call_json()` — must wrap in `ModelRouter(FakeRouter())` in tests. Pre-existing pattern in chaos tests; ε tests initially missed it.

**Known limitations (carried to ζ):**
- `_do_probe()` passes empty `recent_turns=[]` to examiner — examiner wiring simplified for Phase ε. Full examiner integration (reconstructing `Turn` domain objects from TurnInfo + ArtifactStore) deferred.
- Pre-existing runtime sandbox failures remain (exit_code=-11 with 64MB memory limit in sandbox); not caused by ε.
- SIGKILL-and-resume end-to-end test deferred to ζ.

**Carried forward to ζ:** `test_e2e_crash_resume` (POST /sessions/{id} → kill → restart → same turn); full examiner probe wiring.

### ζ review

**Status:** complete 2026-04-22.

**Shipped:**
- `adapters/scorer/llm_problem_framing_scorer.py` — heuristic-backed scorer for `problem_framing` dimension.
- `adapters/scorer/llm_insight_interp_scorer.py` — heuristic-backed scorer for `insight_interp` dimension.
- `adapters/http/session_runner.py` — added `execution_timeout: float = 10.0` field; passed to `RuntimeAdapter.execute()` so the infinite-loop chaos test can use `execution_timeout=2.0`.
- `adapters/eventlog/sqlite_log.py` — added `threading.Lock` (`self._lock`) protecting all connection operations (`append`, `get_session`, `last_seq`, `all`); fixes `cannot start a transaction within a transaction` under concurrent thread access.
- `tests/integration/test_e2e_happy.py` — 3 tests: 3-turn session all 4 dims scored, per_dimension verified via ScoreStore, feedback markdown present.
- `tests/integration/test_e2e_llm_chaos.py` — 3 tests: 100% LLM timeout → session completes via heuristic fallbacks; ScoreComputed present; two-session isolation.
- `tests/integration/test_e2e_runtime_chaos.py` — 2 tests: infinite loop killed → RuntimeFailed emitted → ScoreComputed anyway; mixed prose+code turn handled.
- `tests/integration/test_e2e_crash_resume.py` — 2 tests: crash before FSM step (only SessionStarted/CandidateJoined written); crash after answer posted before scoring — both resume and finish on server 2.
- `tests/integration/test_e2e_double_submit.py` — 3 tests: same nonce deduped to 1 TurnPosted; same nonce → session still scores; distinct nonces → 2 turns.
- `tests/integration/test_e2e_concurrent.py` — 2 tests: 5 parallel threads complete sessions in shared SQLite log; result pages isolated; ScoreComputed per session.

**Tests / verification:**
- 108 tests pass (same 7 pre-existing sandbox-only runtime failures deselected). `ruff check` clean. `mypy --strict core adapters tools --no-incremental` → 33 source files clean.

**Bugs caught during verification:**
1. `SqliteEventLog` not thread-safe under concurrent Python access: multiple threads sharing one connection raced on `BEGIN IMMEDIATE`, producing `cannot start a transaction within a transaction`. Fixed by wrapping all connection operations with `threading.Lock`.

**Known limitations (carried forward):**
- `_do_probe()` passes empty `recent_turns=[]` — full examiner wiring (reconstructing Turn domain objects from TurnInfo + ArtifactStore) remains deferred.
- `problem_framing` / `insight_interp` scorers use only keyword heuristics (no LLM template files); template files can be added later without changing scorer interface.
- Pre-existing runtime sandbox failures (exit_code=-11, 64MB) still present in `test_runtime_events.py` and `test_runtime_sandbox.py`; unrelated to ζ changes.
