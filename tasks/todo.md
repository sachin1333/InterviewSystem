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

---

## Phase η — Voice-First Interview

**Confirmed intent (2026-04-26):**
- Interaction is voice-first: AI interviewer speaks via TTS, candidate responds via Wispr Flow streaming STT, full conversational turn-taking with barge-in.
- Text input panel surfaces only when the candidate is asked to write code, SQL, math, or longer written analysis.
- End-to-end turn latency target: **p50 ≤ 800ms**, **p95 ≤ 1500ms**.
- Question format upgraded to a six-primitive voice-native taxonomy (think_aloud, socratic_rebuttal, counterfactual, resume_deep_dive, verbal_whiteboard, one_bullet) chained into multi-stage cases.
- Cheating defense via timing signature, counterfactual adaptation, recall-vs-recognition asymmetry. Logged as a new `response_authenticity` dimension.

**Out of scope this phase:** real-time video, multi-language voice, on-prem/air-gapped deploy, recruiter-side live observation UI, candidate identity verification (KYC), full PII redaction of audio.

**Architecture:** Wispr Flow (streaming STT) + Cartesia Sonic (streaming TTS, primary) + ElevenLabs Flash (TTS fallback). LLM streaming via OpenRouter for sub-second TTFT. WebSocket bidirectional bridge between browser and FastAPI. Audio artifacts persisted as `ArtifactKind.audio_ref` (already declared); transcripts persisted as `ArtifactKind.markdown` so existing scorers keep working unchanged.

---

### η.0 Research deliverable

The research subagent (2026-04-26) produced a finding set we lock into the plan as decisions, not open questions. Reference summary:

- **Stack:** Wispr Flow STT (WebSocket, 16kHz PCM base64) + Cartesia Sonic TTS (~90ms first byte) primary, ElevenLabs Flash (~75ms) fallback.
- **Latency budget (p50):** VAD 50ms · STT partial 150ms · LLM TTFT 400ms · TTS first byte 100ms · network 100ms = **800ms**.
- **Six question primitives:** think_aloud (90–120s), socratic_rebuttal (60–90s), counterfactual (45–90s), resume_deep_dive (60–120s), verbal_whiteboard (120–180s), one_bullet (15–30s).
- **Multi-stage case shape:** problem_framing → methodology → execution → interpretation → business_synthesis (5 turns, ~13–20 min total).
- **Cheating defense signals:** turn-latency variance, counterfactual freeze, recognition vs recall asymmetry, resume-specific follow-up consistency. Cluster of 3+ raises `response_authenticity` flag.

- [ ] Capture this research into `docs/research/voice-first-interview-2026-04-26.md` so future phases can re-read the source-of-truth without re-doing the search. Include the URL list from the research subagent verbatim.

- [ ] Commit:

```bash
git add docs/research/voice-first-interview-2026-04-26.md
git commit -m "docs: capture voice-first research findings"
```

---

### η.1 Domain & event vocabulary expansion

**Files:**
- Modify: `core/domain.py` (TurnKind, ArtifactKind enums)
- Modify: `core/events.py` (new event types, EVENT_TYPES set)
- Test: `tests/unit/test_voice_events.py`

- [ ] **Step 1: Write the failing test for new TurnKind variants**

`tests/unit/test_voice_events.py`:
```python
from core.domain import TurnKind, ArtifactKind
from core.events import (
    SpeechStarted, SpeechFinalized, AudioChunkAttached,
    LatencyObserved, EVENT_TYPES,
)


def test_voice_turn_kinds_present() -> None:
    assert TurnKind.spoken_question.value == "spoken_question"
    assert TurnKind.spoken_answer.value == "spoken_answer"
    assert TurnKind.spoken_probe.value == "spoken_probe"


def test_voice_artifact_kinds_present() -> None:
    assert ArtifactKind.transcript.value == "transcript"
    assert ArtifactKind.audio_ref.value == "audio_ref"  # already exists


def test_speech_lifecycle_events_registered() -> None:
    for evt in (SpeechStarted, SpeechFinalized, AudioChunkAttached, LatencyObserved):
        assert evt in EVENT_TYPES


def test_speech_finalized_carries_transcript_and_timing() -> None:
    evt = SpeechFinalized(
        turn_id="t-x",
        transcript="hello world",
        wpm=170,
        first_partial_ms=120,
        final_ms=850,
        filler_count=2,
    )
    assert evt.transcript == "hello world"
    assert evt.first_partial_ms == 120
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/unit/test_voice_events.py -v
```
Expected: FAIL — `AttributeError: spoken_question` etc.

- [ ] **Step 3: Add new TurnKind values in `core/domain.py`**

Append after existing `submit = "submit"`:
```python
    spoken_question = "spoken_question"
    spoken_answer = "spoken_answer"
    spoken_probe = "spoken_probe"
```

- [ ] **Step 4: Add `transcript` to `ArtifactKind` in `core/domain.py`**

Append after `audio_ref = "audio_ref"`:
```python
    transcript = "transcript"
```

- [ ] **Step 5: Add new event classes in `core/events.py`** (insert before `EVENT_TYPES` set)

```python
# --- voice / speech lifecycle --------------------------------------------

class SpeechStarted(_Evt):
    """Candidate began speaking; VAD onset."""
    turn_id: str
    started_at_ms: int = Field(ge=0)


class SpeechFinalized(_Evt):
    """Candidate finished speaking; STT final result available."""
    turn_id: str
    transcript: str
    wpm: int = Field(ge=0)
    first_partial_ms: int = Field(ge=0)  # time-to-first-partial-transcript
    final_ms: int = Field(ge=0)          # total speech duration
    filler_count: int = Field(ge=0)      # "um", "uh" tally — feeds authenticity scorer


class AudioChunkAttached(_Evt):
    """Pointer to the persisted audio blob for a turn (artifact_ref pattern)."""
    turn_id: str
    artifact_id: str
    duration_ms: int = Field(ge=0)
    bytes: int = Field(ge=0)


class LatencyObserved(_Evt):
    """Turn-level latency record for cheating-defense + ops dashboard."""
    turn_id: str
    stt_first_partial_ms: int = Field(ge=0)
    stt_final_ms: int = Field(ge=0)
    llm_ttft_ms: int = Field(ge=0)
    tts_first_byte_ms: int = Field(ge=0)
    end_to_end_ms: int = Field(ge=0)
```

- [ ] **Step 6: Register new types in `EVENT_TYPES` set**

Add a section:
```python
    # voice
    SpeechStarted, SpeechFinalized, AudioChunkAttached, LatencyObserved,
```

- [ ] **Step 7: Run tests, verify pass**

```bash
uv run pytest tests/unit/test_voice_events.py -v
uv run mypy --strict core
uv run ruff check core
```

- [ ] **Step 8: Commit**

```bash
git add core/domain.py core/events.py tests/unit/test_voice_events.py
git commit -m "feat(η.1): add voice TurnKinds, transcript ArtifactKind, speech lifecycle events"
```

---

### η.2 Streaming ModelRouter

**Files:**
- Modify: `adapters/llm/router.py` (already accepts `stream` param; provider impls need streaming)
- Modify: `adapters/llm/openai_router.py` / `openrouter_router.py` (real streaming impl)
- Modify: `adapters/llm/fake_router.py` (deterministic chunked stream for tests)
- Test: `tests/unit/test_router_streaming.py`

- [ ] **Step 1: Write failing test for streaming fake router**

`tests/unit/test_router_streaming.py`:
```python
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter


def test_fake_router_streams_token_chunks() -> None:
    fake = FakeRouter(scripted={"_default_": "Hello voice world"})
    router = ModelRouter(fake)
    iterator = router.call(tier="mid", prompt="say hi", stream=True)
    chunks = list(iterator)
    assert "".join(chunks) == "Hello voice world"
    assert len(chunks) > 1, "stream should produce multiple chunks"


def test_streaming_records_first_token_latency(monkeypatch) -> None:
    fake = FakeRouter(scripted={"_default_": "abc def ghi"})
    router = ModelRouter(fake)
    metrics = router.call_streaming_with_metrics(tier="mid", prompt="x")
    text, ttft_ms = metrics.text, metrics.ttft_ms
    assert text == "abc def ghi"
    assert ttft_ms >= 0
```

- [ ] **Step 2: Run test, verify it fails** with `AttributeError: call_streaming_with_metrics`.

- [ ] **Step 3: Implement chunked streaming in `FakeRouter.call`**

Replace fake `call()` with one that, when `stream=True`, yields whitespace-delimited chunks via a generator. Preserve non-streaming behavior unchanged.

- [ ] **Step 4: Add `call_streaming_with_metrics` to `ModelRouter`**

```python
@dataclass(frozen=True)
class StreamingMetrics:
    text: str
    ttft_ms: int
    total_ms: int


def call_streaming_with_metrics(self, *, tier: Tier, prompt: str) -> StreamingMetrics:
    start = time.monotonic()
    iterator = self.call(tier=tier, prompt=prompt, stream=True)
    first = next(iterator, None)
    if first is None:
        return StreamingMetrics(text="", ttft_ms=0, total_ms=0)
    ttft_ms = int((time.monotonic() - start) * 1000)
    rest = "".join(iterator)
    total_ms = int((time.monotonic() - start) * 1000)
    return StreamingMetrics(text=first + rest, ttft_ms=ttft_ms, total_ms=total_ms)
```

- [ ] **Step 5: Implement provider-side streaming in `openrouter_router.py`** using `stream=True` SSE — the existing OpenAI client already supports this; iterate `response` and yield `delta.content`.

- [ ] **Step 6: Run all router tests, verify pass**

```bash
uv run pytest tests/unit/test_router_streaming.py tests/unit/test_model_router.py -v
```

- [ ] **Step 7: Commit**

```bash
git add adapters/llm/ tests/unit/test_router_streaming.py
git commit -m "feat(η.2): streaming ModelRouter with TTFT metrics"
```

---

### η.3 STT adapter — Wispr Flow

**Files:**
- Create: `adapters/stt/__init__.py`
- Create: `adapters/stt/contracts.py` — `Stt` Protocol
- Create: `adapters/stt/wispr_flow.py` — real WebSocket adapter
- Create: `adapters/stt/fake_stt.py` — scripted partials/finals for tests
- Create: `adapters/stt/factory.py` — env-keyed selection
- Test: `tests/unit/test_fake_stt.py`, `tests/chaos/test_stt_failures.py`

- [ ] **Step 1: Define Stt Protocol**

`adapters/stt/contracts.py`:
```python
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SttPartial:
    text: str
    is_final: bool
    elapsed_ms: int


class Stt(Protocol):
    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        sample_rate_hz: int = 16000,
    ) -> AsyncIterator[SttPartial]: ...
```

- [ ] **Step 2: Write failing test for FakeStt**

`tests/unit/test_fake_stt.py`:
```python
import asyncio

from adapters.stt.fake_stt import FakeStt, SttPartial


def test_fake_stt_emits_scripted_partials_then_final() -> None:
    fake = FakeStt(script=[
        SttPartial("hel", is_final=False, elapsed_ms=100),
        SttPartial("hello", is_final=False, elapsed_ms=200),
        SttPartial("hello world", is_final=True, elapsed_ms=550),
    ])

    async def empty_audio():
        for _ in range(3):
            yield b""

    async def run():
        out = []
        async for p in fake.stream(empty_audio()):
            out.append(p)
        return out

    result = asyncio.run(run())
    assert [p.text for p in result] == ["hel", "hello", "hello world"]
    assert result[-1].is_final
```

- [ ] **Step 3: Implement `FakeStt`** in `adapters/stt/fake_stt.py`. Constructor takes a scripted list of `SttPartial`; `stream()` yields them with `asyncio.sleep(0)` between each so back-pressure is realistic.

- [ ] **Step 4: Implement `WisprFlowStt`** in `adapters/stt/wispr_flow.py`:
  - Open WebSocket to `wss://platform-api.wisprflow.ai/api/v1/dash/ws?api_key=Bearer <API_KEY>`.
  - Send first auth message per docs (api_key auth).
  - Forward each base64-encoded PCM 16kHz chunk.
  - Yield `SttPartial(text, is_final, elapsed_ms)` for each server message.
  - Wrap timeouts: if no partial within 3s of first audio, raise `SttTimeout`.

- [ ] **Step 5: Factory in `adapters/stt/factory.py`** — returns `WisprFlowStt` if `WISPR_API_KEY` set, else `FakeStt(_canned_script)`.

- [ ] **Step 6: Chaos tests — `tests/chaos/test_stt_failures.py`**:
  - Connection refused → caller sees `SttConnectionError`, session_runner falls back to text-input prompt.
  - Mid-stream disconnect → partial transcript preserved, `SpeechFinalized` emitted with `transcript=<partial>` and a `degraded=True` marker.
  - Empty audio → no `SpeechFinalized`, candidate re-prompted.

- [ ] **Step 7: Run tests, verify pass**

```bash
uv run pytest tests/unit/test_fake_stt.py tests/chaos/test_stt_failures.py -v
```

- [ ] **Step 8: Commit**

```bash
git add adapters/stt/ tests/unit/test_fake_stt.py tests/chaos/test_stt_failures.py
git commit -m "feat(η.3): Wispr Flow streaming STT adapter + fakes + chaos"
```

---

### η.4 TTS adapter — Cartesia Sonic + ElevenLabs Flash fallback

**Files:**
- Create: `adapters/tts/__init__.py`
- Create: `adapters/tts/contracts.py` — `Tts` Protocol
- Create: `adapters/tts/cartesia_sonic.py`
- Create: `adapters/tts/elevenlabs_flash.py`
- Create: `adapters/tts/fallback_chain.py` — primary→fallback orchestrator
- Create: `adapters/tts/fake_tts.py`
- Test: `tests/unit/test_fake_tts.py`, `tests/chaos/test_tts_failures.py`

- [ ] **Step 1: Define Tts Protocol**

`adapters/tts/contracts.py`:
```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class Tts(Protocol):
    async def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        *,
        voice_id: str,
    ) -> AsyncIterator[bytes]:
        """Yields PCM audio chunks (16kHz, mono, 16-bit) ready for browser playback."""
```

- [ ] **Step 2: Write failing test for FakeTts**

```python
def test_fake_tts_emits_one_audio_chunk_per_sentence() -> None:
    fake = FakeTts(bytes_per_char=2)
    async def text():
        yield "Hi there. How are you?"
    async def run():
        return [c async for c in fake.synthesize(text(), voice_id="v1")]
    chunks = asyncio.run(run())
    assert len(chunks) == 2  # one per sentence
    assert all(isinstance(c, bytes) for c in chunks)
```

- [ ] **Step 3: Implement `FakeTts`** producing `b"\x00" * (len(sentence) * bytes_per_char)` per sentence.

- [ ] **Step 4: Implement `CartesiaSonicTts`** — POST chunked stream to `https://api.cartesia.ai/tts/sse` with model `sonic-english`, returning audio chunks as they arrive. First-byte target ≤100ms.

- [ ] **Step 5: Implement `ElevenLabsFlashTts`** — POST stream to `https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream` with model `eleven_flash_v2_5`. First-byte target ≤120ms.

- [ ] **Step 6: Implement `FallbackChainTts`** — primary `CartesiaSonicTts`; if first byte not received within 250ms or HTTP error, switch to `ElevenLabsFlashTts`. Emit a `TierFallback` event.

- [ ] **Step 7: Chaos tests — `tests/chaos/test_tts_failures.py`**:
  - Primary 5xx → fallback used; output bytes still arrive.
  - Both providers down → `TtsAllFailed`; session_runner switches to text-only display fallback (read on-screen).

- [ ] **Step 8: Run tests, verify pass**

```bash
uv run pytest tests/unit/test_fake_tts.py tests/chaos/test_tts_failures.py -v
```

- [ ] **Step 9: Commit**

```bash
git add adapters/tts/ tests/unit/test_fake_tts.py tests/chaos/test_tts_failures.py
git commit -m "feat(η.4): Cartesia Sonic + ElevenLabs Flash TTS chain"
```

---

### η.5 Question primitive library (format upgrade)

**Files:**
- Create: `core/primitives.py` — six primitives as typed enums + metadata
- Create: `templates/agents/challenger/PRIMITIVES.md`
- Create: `templates/agents/examiner/PRIMITIVES.md`
- Create: `templates/cases/multi_stage_case_v1.yaml` — five-stage shape
- Modify: `adapters/challenger/llm_challenger.py` — emit primitive-tagged prompts
- Modify: `adapters/examiner/llm_examiner.py` — pick probe primitive based on prior turn
- Test: `tests/unit/test_primitives.py`, `tests/unit/test_case_decomposition.py`

- [ ] **Step 1: Define primitives in `core/primitives.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.domain import Dimension


class Primitive(StrEnum):
    think_aloud = "think_aloud"
    socratic_rebuttal = "socratic_rebuttal"
    counterfactual = "counterfactual"
    resume_deep_dive = "resume_deep_dive"
    verbal_whiteboard = "verbal_whiteboard"
    one_bullet = "one_bullet"


@dataclass(frozen=True)
class PrimitiveSpec:
    primitive: Primitive
    expected_seconds_min: int
    expected_seconds_max: int
    primary_signals: tuple[Dimension, ...]
    description: str
    cheating_defense: bool  # primitives that double as trapdoors


PRIMITIVE_REGISTRY: dict[Primitive, PrimitiveSpec] = {
    Primitive.think_aloud: PrimitiveSpec(
        primitive=Primitive.think_aloud,
        expected_seconds_min=90, expected_seconds_max=120,
        primary_signals=(Dimension.problem_framing, Dimension.insight_interp),
        description="Force candidate to externalize first-five-steps reasoning out loud.",
        cheating_defense=False,
    ),
    Primitive.socratic_rebuttal: PrimitiveSpec(
        primitive=Primitive.socratic_rebuttal,
        expected_seconds_min=60, expected_seconds_max=90,
        primary_signals=(Dimension.model_rationale, Dimension.communication),
        description="Challenge a stated method choice; require defense or pivot.",
        cheating_defense=True,
    ),
    Primitive.counterfactual: PrimitiveSpec(
        primitive=Primitive.counterfactual,
        expected_seconds_min=45, expected_seconds_max=90,
        primary_signals=(Dimension.model_rationale, Dimension.problem_framing),
        description="Inject 'assume X changed' to disrupt cached reasoning.",
        cheating_defense=True,
    ),
    Primitive.resume_deep_dive: PrimitiveSpec(
        primitive=Primitive.resume_deep_dive,
        expected_seconds_min=60, expected_seconds_max=120,
        primary_signals=(Dimension.problem_framing,),
        description="Ask for class imbalance ratios / dataset shapes from a stated prior project.",
        cheating_defense=True,
    ),
    Primitive.verbal_whiteboard: PrimitiveSpec(
        primitive=Primitive.verbal_whiteboard,
        expected_seconds_min=120, expected_seconds_max=180,
        primary_signals=(Dimension.experiment_design, Dimension.communication),
        description="Describe a multi-stage pipeline (e.g., retraining loop) in words only.",
        cheating_defense=False,
    ),
    Primitive.one_bullet: PrimitiveSpec(
        primitive=Primitive.one_bullet,
        expected_seconds_min=15, expected_seconds_max=30,
        primary_signals=(Dimension.communication,),
        description="One sentence: why does this metric matter?",
        cheating_defense=False,
    ),
}
```

- [ ] **Step 2: Write failing test**

`tests/unit/test_primitives.py`:
```python
from core.primitives import Primitive, PRIMITIVE_REGISTRY


def test_all_primitives_have_specs() -> None:
    assert set(PRIMITIVE_REGISTRY.keys()) == set(Primitive)


def test_primitives_with_cheating_defense_flagged() -> None:
    cheat_defense = {p for p, s in PRIMITIVE_REGISTRY.items() if s.cheating_defense}
    assert cheat_defense == {
        Primitive.socratic_rebuttal,
        Primitive.counterfactual,
        Primitive.resume_deep_dive,
    }


def test_one_bullet_is_shortest() -> None:
    durations = {p: s.expected_seconds_max for p, s in PRIMITIVE_REGISTRY.items()}
    assert durations[Primitive.one_bullet] == min(durations.values())
```

- [ ] **Step 3: Run, verify pass.**

- [ ] **Step 4: Define multi-stage case in `templates/cases/multi_stage_case_v1.yaml`**

```yaml
version: "1.0"
name: "Five-Stage DS/ML Case"
total_duration_min: 18
stages:
  - id: problem_framing
    primitive: think_aloud
    duration_s: 120
    prompt_seed: "Restate the business problem and break it into 3 subproblems."
    on_complete: methodology

  - id: methodology
    primitive: socratic_rebuttal
    duration_s: 75
    prompt_seed: "Defend your model choice. What would break it?"
    on_complete: execution

  - id: execution
    primitive: verbal_whiteboard
    duration_s: 150
    prompt_seed: "Walk through your validation strategy step by step."
    on_complete: interpretation

  - id: interpretation
    primitive: counterfactual
    duration_s: 60
    prompt_seed: "Assume precision drops 20%. What does that signal?"
    on_complete: synthesis

  - id: synthesis
    primitive: one_bullet
    duration_s: 25
    prompt_seed: "One sentence — explain this to a non-technical exec."
    on_complete: end
```

- [ ] **Step 5: Persona files — write `templates/agents/challenger/PRIMITIVES.md` and `templates/agents/examiner/PRIMITIVES.md`** using the registry as ground truth. Examiner picks `socratic_rebuttal` after methodology turns, `counterfactual` after execution, etc., per the case YAML.

- [ ] **Step 6: Modify `adapters/challenger/llm_challenger.py`** to accept a `primitive: Primitive` parameter and load case-stage prompts from the YAML. Default `think_aloud` for first-question backward compat.

- [ ] **Step 7: Modify `adapters/examiner/llm_examiner.py`** so probe selection reads `case_stage` from session state and picks the matching primitive.

- [ ] **Step 8: Add `tests/unit/test_case_decomposition.py`** asserting that walking the YAML produces the expected 5-step primitive sequence.

- [ ] **Step 9: Commit**

```bash
git add core/primitives.py templates/agents/challenger/PRIMITIVES.md templates/agents/examiner/PRIMITIVES.md templates/cases/multi_stage_case_v1.yaml adapters/challenger/llm_challenger.py adapters/examiner/llm_examiner.py tests/unit/test_primitives.py tests/unit/test_case_decomposition.py
git commit -m "feat(η.5): six-primitive question taxonomy + multi-stage case"
```

---

### η.6 Voice session runner — streaming pipeline

**Files:**
- Create: `adapters/http/voice_runner.py` — sibling to `session_runner.py`, async, owns voice turn lifecycle
- Modify: `adapters/http/session_runner.py` — `RunResult` gains optional `audio_stream: AsyncIterator[bytes] | None` and `case_stage: str | None`
- Test: `tests/unit/test_voice_runner.py`

- [ ] **Step 1: Write failing test for VoiceRunner happy path**

```python
async def test_voice_runner_full_turn_round_trip(tmp_path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter(scripted={"_default_": "Walk me through your approach."}))
    stt = FakeStt(script=[SttPartial("I would start with EDA", is_final=True, elapsed_ms=600)])
    tts = FakeTts(bytes_per_char=2)
    runner = VoiceRunner(log=log, router=router, stt=stt, tts=tts, ...)
    session_id = await runner.start_session(rubric_version="ds-ml-v1")
    audio_in = empty_async_iter()
    result = await runner.next_turn(session_id, candidate_audio=audio_in)
    assert result.case_stage == "problem_framing"
    assert result.transcript == "I would start with EDA"
    audio_out = b"".join([c async for c in result.audio_stream])
    assert len(audio_out) > 0
    # latency event recorded
    events = log.get_session(session_id)
    assert any(isinstance(e.payload, LatencyObserved) for e in events)
```

- [ ] **Step 2: Implement `VoiceRunner.next_turn` with the streaming pipeline:**
  1. Open STT stream on candidate audio chunks; record `SpeechStarted`.
  2. As partials arrive, when `is_final=True`, capture `transcript`, `first_partial_ms`, `final_ms`.
  3. Emit `TurnPosted(actor=candidate, kind=spoken_answer)` + `ArtifactAttached(kind=transcript, content=transcript)` + `AudioChunkAttached`.
  4. Read case YAML; pick next primitive based on prior turn.
  5. Stream LLM response via `router.call(stream=True)`; record `llm_ttft_ms` on first chunk.
  6. Pipe streamed text into TTS; record `tts_first_byte_ms`.
  7. Emit `TurnPosted(actor=examiner|challenger, kind=spoken_probe|spoken_question)` + transcript artifact.
  8. Emit `LatencyObserved` covering all four stages + end-to-end.

- [ ] **Step 3: Implement barge-in:** a `BargeInDetected` callback on the audio out stream that cancels the in-flight TTS chunk if VAD detects candidate speech. Test with a fixture that triggers mid-synthesis.

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/unit/test_voice_runner.py -v
```

- [ ] **Step 5: Commit**

```bash
git add adapters/http/voice_runner.py adapters/http/session_runner.py tests/unit/test_voice_runner.py
git commit -m "feat(η.6): VoiceRunner with streaming STT→LLM→TTS pipeline + barge-in"
```

---

### η.7 WebSocket bridge in FastAPI

**Files:**
- Modify: `adapters/http/app.py` — add WebSocket route `/ws/sessions/{session_id}/voice`
- Create: `adapters/http/voice_protocol.py` — JSON message schema (audio_chunk, partial_transcript, final_transcript, tts_chunk, mode_switch)
- Test: `tests/integration/test_voice_ws.py` (using FastAPI TestClient WebSocket support)

- [ ] **Step 1: Define wire protocol in `voice_protocol.py`**

```python
from typing import Literal, TypedDict


class ClientAudioChunk(TypedDict):
    type: Literal["audio_chunk"]
    seq: int
    pcm_b64: str


class ClientModeSwitch(TypedDict):
    """Candidate flips to typing for code/SQL/math/long-form."""
    type: Literal["mode_switch"]
    mode: Literal["voice", "text"]


class ServerPartialTranscript(TypedDict):
    type: Literal["partial_transcript"]
    text: str
    is_final: bool


class ServerTtsChunk(TypedDict):
    type: Literal["tts_chunk"]
    pcm_b64: str
    end_of_utterance: bool


class ServerStageChange(TypedDict):
    type: Literal["stage_change"]
    case_stage: str
    primitive: str
    show_text_panel: bool  # true when current primitive needs typed input
```

- [ ] **Step 2: Write failing test exercising WS round-trip**

`tests/integration/test_voice_ws.py`:
```python
def test_voice_ws_roundtrip(tmp_path):
    app = make_app_with_fakes(tmp_path)
    client = TestClient(app)
    with client.websocket_connect("/ws/sessions/s1/voice") as ws:
        ws.send_json({"type": "audio_chunk", "seq": 1, "pcm_b64": "AAAA"})
        msg = ws.receive_json()
        assert msg["type"] == "partial_transcript"
        # eventually a stage_change arrives
        while True:
            m = ws.receive_json()
            if m["type"] == "stage_change":
                assert m["case_stage"] in {"problem_framing", "methodology"}
                break
```

- [ ] **Step 3: Implement WS handler** in `adapters/http/app.py`:
  - Accept connection, authenticate via session_id cookie.
  - Reads candidate audio chunks → forwards to `VoiceRunner.next_turn`.
  - Sends partials, finals, TTS chunks, stage_change messages back.
  - Handles `mode_switch` to "text" by closing voice stream and falling back to existing HTML form (302 redirect on next POST).

- [ ] **Step 4: Run tests, verify pass.**

- [ ] **Step 5: Commit**

```bash
git add adapters/http/app.py adapters/http/voice_protocol.py tests/integration/test_voice_ws.py
git commit -m "feat(η.7): WebSocket voice bridge with text-mode escape hatch"
```

---

### η.8 Frontend — voice mode in `turn.html`

**Files:**
- Modify: `adapters/http/templates/turn.html` — add voice section + JS
- Create: `adapters/http/static/voice.js` — single-file vanilla JS, no framework
- Create: `adapters/http/static/voice.css` — minimal styles

- [ ] **Step 1: Add `<script>` mount in `turn.html` under existing form** — voice UI is the default; existing textarea form is hidden behind `<details data-mode="text">` and revealed when server sends `show_text_panel: true` or candidate clicks "Switch to typing".

- [ ] **Step 2: Implement `voice.js`:**
  - `getUserMedia({audio:true})` for mic.
  - `AudioContext` + `ScriptProcessorNode` (or `AudioWorklet` for modern browsers) downsamples to 16kHz mono PCM.
  - Send 100ms chunks as base64 over WebSocket.
  - Render server partials in a transcript bubble.
  - Pipe `tts_chunk` PCM into `AudioBufferSourceNode` for low-latency playback.
  - On `stage_change` with `show_text_panel: true`: reveal textarea, focus it, label "Type your code/analysis here".
  - VAD: if mic level crosses threshold while TTS playing → cancel current `AudioBufferSourceNode` (barge-in).

- [ ] **Step 3: Manual smoke test checklist** (add to `tasks/smoke.md`):
  - Open `/sessions/{id}` in Chrome → mic permission prompt → speak → see partial text appearing → see AI voice reply within ~1s → barge-in interrupts AI mid-sentence → text panel appears for `verbal_whiteboard`/code primitives.

- [ ] **Step 4: Commit**

```bash
git add adapters/http/templates/turn.html adapters/http/static/
git commit -m "feat(η.8): voice mode UI with VAD, barge-in, text-panel escape"
```

---

### η.9 Cheating defense — response authenticity scorer

**Files:**
- Modify: `core/domain.py` — add `Dimension.response_authenticity = "response_authenticity"`
- Modify: `templates/rubrics/ds-ml-engineer-v1.yaml` — add new dimension with weight (rebalance others)
- Create: `adapters/scorer/authenticity_scorer.py` — heuristic + LLM-backed
- Test: `tests/unit/test_authenticity_scorer.py`, `tests/chaos/test_ai_coached_candidate.py`

- [x] **Step 1: Add new Dimension and rubric weight (sum back to 1.0).**

Proposed weights: problem_framing=0.18, model_rationale=0.22, experiment_design=0.15, insight_interp=0.15, communication=0.18, response_authenticity=0.12. Document the choice in `templates/rubrics/CHANGELOG.md`.

- [x] **Step 2: Write failing test for authenticity scoring**

```python
def test_low_latency_variance_reduces_authenticity() -> None:
    # AI-coached candidate: every turn lands at ~3s, regardless of difficulty
    latencies = [LatencyObserved(turn_id=f"t{i}", stt_first_partial_ms=300,
                                  stt_final_ms=500, llm_ttft_ms=0,
                                  tts_first_byte_ms=0, end_to_end_ms=3000)
                 for i in range(5)]
    fillers = [SpeechFinalized(turn_id=f"t{i}", transcript="...", wpm=160,
                                first_partial_ms=300, final_ms=2700,
                                filler_count=0) for i in range(5)]
    signal = AuthenticityScorer().score(latencies, fillers, counterfactual_handled=False)
    assert signal.value < 0.4
    assert signal.confidence > 0.6
```

- [x] **Step 3: Implement `AuthenticityScorer`:**
  - Compute end-to-end latency variance (low variance + zero filler words on hard questions = suspicious).
  - Track counterfactual handling: was a `Primitive.counterfactual` probe answered with fluent reasoning, or with delay+repetition?
  - Track resume-deep-dive consistency: did candidate produce specific numbers/ratios when asked about CV claims?
  - Combine three signals into a single 0–1 score with a confidence based on number of evidence points.

- [x] **Step 4: Add chaos test that simulates an AI-coached candidate** (constant latency, zero fillers, generic counterfactual response) and asserts authenticity score < 0.5.

- [x] **Step 5: Run tests, verify pass.**

- [x] **Step 6: Commit** *(covered by consolidated η commit)*

```bash
git add core/domain.py templates/rubrics/ adapters/scorer/authenticity_scorer.py tests/unit/test_authenticity_scorer.py tests/chaos/test_ai_coached_candidate.py
git commit -m "feat(η.9): response_authenticity dimension + heuristic scorer"
```

---

### η.10 Communication scorer upgrade for voice

**Files:**
- Modify: `adapters/scorer/llm_communication_scorer.py` — accept transcript + filler_count + WPM as inputs, not just markdown body

- [x] **Step 1: Write failing test asserting filler-rate penalty**

```python
def test_communication_penalizes_high_filler_rate() -> None:
    transcript = "um so like uh I think um the model um could be"  # 4 fillers / 11 words ~ 36%
    filler_count = 4
    wpm = 110
    score = LlmCommunicationScorer(...).score_voice(transcript, filler_count, wpm)
    assert score.value < 0.5
```

- [x] **Step 2: Add `score_voice(transcript, filler_count, wpm)` method.** Heuristic: penalty = clamp(filler_count / max(word_count,1) * 2, 0, 0.4); subtract from LLM-rated baseline.

- [x] **Step 3: Wire VoiceRunner to call `score_voice` after `SpeechFinalized` events for spoken turns.**

- [x] **Step 4: Run tests, commit** *(covered by consolidated η commit)*

```bash
git add adapters/scorer/llm_communication_scorer.py tests/unit/test_communication_voice.py
git commit -m "feat(η.10): communication scorer reads filler rate + WPM for voice"
```

---

### η.11 Bootstrap & feature flag

**Files:**
- Modify: `adapters/http/app.py` `create_app()` — read `VOICE_MODE` env (`off|on|forced`)
- Modify: `README.md` — voice setup instructions, env vars table

- [x] **Step 1: Add env-keyed wiring:**
  - `VOICE_MODE=off` (default) → existing text-only path, unchanged.
  - `VOICE_MODE=on` → renders voice UI, candidate can switch to text via UI button.
  - `VOICE_MODE=forced` → text panel only appears when primitive demands it.
  - `WISPR_API_KEY`, `CARTESIA_API_KEY`, `ELEVENLABS_API_KEY` → real provider selection. Missing → fakes.

- [x] **Step 2: README env-var table:**

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `VOICE_MODE` | no | `off` | Voice UI visibility |
| `WISPR_API_KEY` | when `VOICE_MODE!=off` | — | STT |
| `CARTESIA_API_KEY` | optional | — | Primary TTS |
| `ELEVENLABS_API_KEY` | optional | — | Fallback TTS |
| `VOICE_LATENCY_BUDGET_MS` | no | `800` | Soft target; logged when exceeded |

- [x] **Step 3: Smoke check:**

```bash
VOICE_MODE=off uv run uvicorn adapters.http.app:create_app --factory  # behaves as before
VOICE_MODE=on uv run uvicorn adapters.http.app:create_app --factory  # voice UI loads
```

- [x] **Step 4: Commit** *(covered by consolidated η commit)*

```bash
git add adapters/http/app.py README.md
git commit -m "feat(η.11): VOICE_MODE feature flag + bootstrap wiring"
```

---

### η.12 End-to-end voice integration tests

**Files:**
- Create: `tests/integration/test_e2e_voice_happy.py`
- Create: `tests/integration/test_e2e_voice_chaos.py`
- Create: `tests/integration/test_e2e_voice_latency_budget.py`
- Create: `tests/integration/test_e2e_voice_text_fallback.py`

- [x] **Step 1: Happy path** — full 5-stage case via fake STT + fake LLM stream + fake TTS. Assert `LatencyObserved` events present for every turn; assert each stage’s primitive matches `multi_stage_case_v1.yaml`.

- [x] **Step 2: Chaos** — Wispr Flow timeout mid-stream → session emits `SpeechFinalized` with `degraded=True` → next probe still proceeds. Cartesia 5xx → ElevenLabs takes over → `TierFallback` event recorded.

- [x] **Step 3: Latency budget** — synthetic monotonic clock; assert end-to-end p50 ≤ 800ms across 10 fake turns. Fail fast if regression introduced.

- [x] **Step 4: Text fallback** — candidate clicks "Switch to typing" mid-session; voice WebSocket closes; existing POST /sessions/{id}/turn flow accepts the typed answer; session continues to completion.

- [x] **Step 5: Run all integration tests, verify pass:**

```bash
uv run pytest tests/integration/ -v -k voice
```

- [x] **Step 6: Commit** *(covered by consolidated η commit)*

```bash
git add tests/integration/test_e2e_voice_*.py
git commit -m "test(η.12): e2e voice — happy, chaos, latency budget, text fallback"
```

---

### η.13 Latency dashboard

**Files:**
- Create: `tools/voice_latency_report.py` — reads `LatencyObserved` events from a SqliteEventLog; emits markdown table p50/p95 per stage.

- [x] **Step 1: Implement reader + percentile math.**

- [x] **Step 2: Add CLI:**

```bash
uv run python -m tools.voice_latency_report --db interview.db --since 1h
```

Expected output: markdown table with columns `stt_first_partial_ms | llm_ttft_ms | tts_first_byte_ms | end_to_end_ms` and rows `p50 | p95 | max`.

- [x] **Step 3: Commit** *(covered by consolidated η commit)*

```bash
git add tools/voice_latency_report.py
git commit -m "feat(η.13): voice latency report CLI"
```

---

**Exit gate η:**
1. `uv run pytest -v` → all green (incl. new η tests).
2. `uv run mypy --strict core adapters tools` clean.
3. `uv run ruff check .` clean.
4. Manual smoke: `VOICE_MODE=on` end-to-end browser session → 5-stage case → text panel auto-appears for `verbal_whiteboard` → barge-in works → final feedback markdown emitted.
5. `tools/voice_latency_report.py` shows p50 ≤ 800ms across 5 manual sessions.
6. `tests/chaos/test_ai_coached_candidate.py` produces authenticity score < 0.5 on the simulated coached transcript.

---

### η review

**Status:** implementation slice complete 2026-04-26.

**Shipped:**
- `core/domain.py` — added `Dimension.response_authenticity`.
- `templates/rubrics/ds-ml-engineer-v1.yaml` + `templates/rubrics/CHANGELOG.md` — rebalanced the rubric for voice interviews and documented the weighting rationale.
- `adapters/scorer/authenticity_scorer.py` — deterministic authenticity scorer using latency variance, speech texture, counterfactual adaptation, and resume-specificity signals.
- `adapters/scorer/llm_communication_scorer.py` — new `score_voice(...)` path that applies filler-rate / WPM penalties on top of the existing baseline.
- `adapters/http/voice_runner.py` — spoken-turn communication scoring, authenticity scoring, degraded STT handling, and TTS fallback-event emission.
- `adapters/http/app.py`, `adapters/http/templates/turn.html`, `adapters/http/static/voice.js`, `.env.example`, `README.md` — `VOICE_MODE=off|on|forced`, fake-provider fallback wiring, text-mode escape hatch, and setup docs.
- `tests/integration/test_e2e_voice_{happy,chaos,latency_budget,text_fallback}.py` — voice happy path, degraded STT, TTS fallback, latency budget, and typed fallback coverage.
- `tools/voice_latency_report.py` — markdown latency dashboard CLI.

**Verification:**
- `uv run pytest -q` ✅
- `mypy --strict core adapters tools` ✅
- `ruff check .` ✅
- `VOICE_MODE=off` / `VOICE_MODE=on` app bootstrap smoke via `create_app(...)` ✅
- `tools.voice_latency_report` markdown output smoke ✅
- `tests/chaos/test_ai_coached_candidate.py` keeps authenticity below 0.5 for the coached-profile fixture ✅

**Notes:**
- I used one consolidated local git commit for η.9–η.13 instead of the per-subsection commits in the checklist.
- Browser/microphone manual smoke is still worth doing before calling the whole voice phase production-ready, but the automated verification gate is green.
