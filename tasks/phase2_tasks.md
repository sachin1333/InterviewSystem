# Phase 2 — Granular Task List

Derived from `tasks/phase2_spec.md` (2026-04-28). Each task is intended to be a single focused unit of work.

**Complexity legend**
- **[easy]** — ~30–90 min, one file or trivial cross-cutting, mechanical, low design risk.
- **[medium]** — ~half-day, multiple files, requires design judgment and tests.
- **[hard]** — ~1–2 days, cross-cutting, requires a non-obvious design decision and a robust test plan.

Order within each phase is the suggested execution order. Sub-phases run in the spec's order (2.1 → 2.7) with 2.5 / 2.6 / 2.7 partly parallelizable after 2.2 lands.

---

## Phase 2.1 — Multi-problem domain model

- [x] **2.1.1** [easy] Add `Problem` dataclass to `core/domain.py` with fields: `id`, `opener_text`, `context` (examiner-only), `target_dimensions: tuple[Dimension, ...]`, `dim_thresholds: Mapping[Dimension, float]`, `expected_duration_s`.
- [x] **2.1.2** [easy] Add `ProblemIntroduced` event type with fields `problem_id`, `opener_text`, `ordinal`.
- [x] **2.1.3** [easy] Add `ProblemClosed` event type with fields `problem_id`, `reason: Literal["coverage_saturated", "time_capped", "examiner_pivot", "max_probes"]`, `rationale: str`.
- [x] **2.1.4** [medium] Extend the `Session` projection to track `problems: tuple[Problem, ...]`, `current_problem_id: ProblemId | None`, `problem_status: Mapping[ProblemId, Status]`.
- [x] **2.1.5** [medium] SQLite event-log schema migration: register new event kinds, bump log schema version, keep backward-compat read path for legacy `StageEntered/Completed` rows.
- [x] **2.1.6** [medium] Add FSM invariants for problem ordering: `ProblemIntroduced` precedes any turn; `ProblemClosed(N)` precedes `ProblemIntroduced(N+1)`; `SessionEnded` follows the last `ProblemClosed`.
- [x] **2.1.7** [medium] Introduce `ProblemSequencer` service: on `ProblemClosed`, emits `ProblemIntroduced(next)` from the planned list, or `SessionEnded` when the list is exhausted.
- [x] **2.1.8** [medium] Disconnect `StageEntered`/`StageCompleted` from FSM control flow in `adapters/http/session_runner.py`. Keep emission for analytics; remove every branch that reads them.
- [x] **2.1.9** [medium] Update `core/orchestrator.py` to consume problem boundaries instead of stage boundaries when deciding the next action.
- [x] **2.1.10** [medium] Replace `session_runner.case: CaseDefinition | None` with `problems: list[Problem]` plus `current_idx: int`.
- [x] **2.1.11** [easy] Unit tests: building a `Session` with three problems, verifying event ordering invariants, projection correctness on `ProblemIntroduced/Closed`.
- [x] **2.1.12** [medium] Replay test: a recorded Phase α session with legacy stage events replays cleanly under the new orchestrator without errors.
- [x] **2.1.13** [easy] Update `core/invariants.md` to document the Problem-based FSM contract; mark stage events as legacy / replay-only.

## Phase 2.2 — Coverage tracker + free-form examiner

- [ ] **2.2.1** [medium] Create `core/coverage.py` with a `CoverageTracker`: `record(problem_id, dim, signal: float)`, `current(problem_id) -> Mapping[Dimension, float]`, `under_served(problem_id, thresholds) -> list[Dimension]`.
- [ ] **2.2.2** [medium] Define signal-strength normalization: scorers emit 0..1 confidence per dim per turn; tracker accumulates with a diminishing-returns curve so one strong turn does not saturate.
- [ ] **2.2.3** [medium] Wire each scorer in `core/orchestrator.py` to feed the `CoverageTracker` after every scored turn.
- [ ] **2.2.4** [easy] Add `CoverageSnapshot` event capturing the dim → signal map at the moment a probe is generated (for replay and audit).
- [ ] **2.2.5** [easy] Rewrite `templates/agents/examiner/IDENTITY.md` for the free-form probe role; remove all stage references.
- [ ] **2.2.6** [easy] Rewrite `templates/agents/examiner/SOUL.md` for examiner-as-driver mindset; encourage pivoting and closing on coverage saturation.
- [ ] **2.2.7** [easy] Rewrite `templates/agents/examiner/TOOLS.md` to document the new tool surface: `propose_probe(text)` and `close_problem(reason)`.
- [ ] **2.2.8** [easy] Rewrite `templates/agents/examiner/PACING.md`: remove stage budgets, replace with coverage-based pacing guidance.
- [ ] **2.2.9** [easy] Define examiner output JSON schema: `{"action": "probe" | "close", "text"?: str, "reason"?: enum, "rationale": str, "primitive_hint"?: str}`.
- [ ] **2.2.10** [hard] Update `LlmExaminer.review` and `iter_review` to emit either a probe or a close decision; preserve token streaming on probe text; tolerate malformed JSON with a one-shot retry-then-fail-loud.
- [ ] **2.2.11** [medium] Inject coverage state into the examiner prompt: pass `under_served` dims, the accumulated signal map, current probe count, and remaining-dim targets.
- [ ] **2.2.12** [medium] Inject transcript context: full transcript scoped to the active problem only (not the whole session), to control prompt size.
- [ ] **2.2.13** [easy] Delete the primitive→stage coupling in the examiner path: remove primitive lookup in `session_runner._do_probe` and any constructor wiring that depended on it.
- [ ] **2.2.14** [easy] Demote `core/primitives.py` to optional metadata: examiner may attach a `primitive_hint` to a probe; nothing in control flow consumes it.
- [ ] **2.2.15** [medium] Implement `close_problem` handler in `session_runner`: finalize the problem, emit `ProblemClosed`, hand off to `ProblemSequencer`.
- [ ] **2.2.16** [easy] Add per-problem safety cap: force close after `MAX_PROBES_PER_PROBLEM` (config, default 6) regardless of coverage; emits `reason: "max_probes"`.
- [ ] **2.2.17** [medium] Eval suite — probe adapts to candidate specifics: generate probes for five distinct candidate answers to the same opener, assert pairwise distinct semantic content (cosine-distance threshold).
- [ ] **2.2.18** [medium] Eval suite — probe targets under-covered dims: seed coverage state with one dim under-served, assert the next probe focuses on that dim.
- [ ] **2.2.19** [medium] Eval suite — examiner closes on saturation: feed answers that saturate every targeted dim, assert `close_problem` emitted before the safety cap fires.

## Phase 2.3 — Problem bank

- [ ] **2.3.1** [easy] Define problem-bank YAML schema: `id`, `opener`, `context`, `target_dimensions`, per-dim thresholds, `expected_duration_s`, `tags`.
- [ ] **2.3.2** [medium] Add `core/problem_bank.py` with loader, schema validator, and a deterministic picker keyed off `session_id` hash.
- [ ] **2.3.3** [medium] Author Problem 1 in `templates/problem_banks/ds-ml-engineer-v1.yaml` — framing & data-cleaning emphasis (weights `problem_framing`, `communication`).
- [ ] **2.3.4** [medium] Author Problem 2 — model selection / rationale emphasis (`model_rationale`, `experiment_design`).
- [ ] **2.3.5** [medium] Author Problem 3 — experimental design / A-B emphasis (`experiment_design`, `insight_interp`).
- [ ] **2.3.6** [medium] Author Problem 4 — stakeholder communication emphasis (`communication`, `insight_interp`).
- [ ] **2.3.7** [easy] Schema-validation tests: malformed bank entries fail loudly with actionable error messages.
- [ ] **2.3.8** [medium] Wire `ProblemSequencer` to draw a 3–4 problem sequence from the bank at session start, balanced across rubric dims.
- [ ] **2.3.9** [medium] Migrate `core/case_bank.py` first-prompt path to read openers from the problem bank for new sessions; keep `CaseBank` for legacy session replay only.
- [ ] **2.3.10** [easy] Cold-start cache: pre-warm openers across the bank at app boot, target sub-500ms first-paint per opener.
- [ ] **2.3.11** [easy] Picker fairness test: across 1000 synthetic `session_id`s, problem distribution is roughly uniform within tolerance.

## Phase 2.4 — UI continuous-chat + mode toggle

- [ ] **2.4.1** [easy] Strip stage-progress chrome from `templates/turn.html` (and any partials that render stage indicators).
- [ ] **2.4.2** [easy] Add a soft "Problem N of M" header that updates on `ProblemIntroduced` events.
- [ ] **2.4.3** [medium] Render the full conversation as a bubble thread, newest-bottom, full-history scroll; reuse the θ.2 EventSource bubble component.
- [ ] **2.4.4** [easy] Persistent text input pinned to the bottom of the chat; `Enter` submits, `Shift+Enter` newline.
- [ ] **2.4.5** [medium] Mic toggle button next to text input; pressing it opens the WS voice session for the next answer (and persists until toggled back).
- [ ] **2.4.6** [medium] Mid-answer mode switch: handle `ClientModeSwitch` from voice→text without losing partial transcript state.
- [ ] **2.4.7** [easy] "Thinking…" affordance shown while waiting for the first SSE token of the next question.
- [ ] **2.4.8** [easy] Verify streaming probe paint still works inside the new chat layout (regression check on θ.2 behavior).
- [ ] **2.4.9** [easy] Problem-boundary visual: subtle divider in the thread when a new problem is introduced.
- [ ] **2.4.10** [medium] Accessibility pass: keyboard nav, screen-reader announcement on new question, ARIA labels on mic toggle, focus management across mode switch.
- [ ] **2.4.11** [hard] End-to-end browser test: candidate completes three problems, switches input mode mid-session, transcript and event log remain consistent.

## Phase 2.5 — Latency: challenger streaming + probe-context prefetch

- [ ] **2.5.1** [medium] Extend `iter_streaming` usage to challenger turns (problem openers when not cached, plus any examiner-introduced new-problem text).
- [ ] **2.5.2** [medium] Prefetch trigger: when candidate starts typing (debounced) or VAD detects sustained speech, schedule a background context-build task.
- [ ] **2.5.3** [medium] Background task — transcript summarization for the active problem (heuristic for short transcripts, cheap LLM call when length crosses threshold).
- [ ] **2.5.4** [easy] Background task — coverage diff pre-staged so the probe call only needs the model invocation.
- [ ] **2.5.5** [medium] Cache the prefetched context per turn; invalidate on candidate-submit only if the answer changed materially since prefetch (length delta + content hash).
- [ ] **2.5.6** [medium] Timing instrumentation: emit per-turn events with `submit_received_ms`, `context_assembled_ms`, `first_token_ms`, `first_paint_ms`.
- [ ] **2.5.7** [medium] Timing dashboard: aggregate the timing events, render p50/p95 per stage, threshold-alert on regression beyond budget.
- [ ] **2.5.8** [medium] Load test: 20-turn synthetic session must hit p50 ≤ 800ms and p95 ≤ 1500ms; failing the budget fails the test.
- [ ] **2.5.9** [medium] Cold-path fallback: if prefetch fails or is stale, the hot path still works and the candidate sees no error.

## Phase 2.6 — Per-problem score breakdown

- [ ] **2.6.1** [medium] Aggregator emits per-problem scores in addition to the session aggregate; events persisted in the log.
- [ ] **2.6.2** [easy] Persist per-problem score events with proper indexing for dashboard reads.
- [ ] **2.6.3** [medium] Recruiter dashboard: render per-problem breakdown side-by-side with the session aggregate; per-dim bars per problem.
- [ ] **2.6.4** [hard] Business-acumen decision: analyze 10 sample sessions, measure how often `insight_interp` covers business-judgment signal, decide split / rename / keep, document conclusion.
- [ ] **2.6.5** [medium] Pinned regression session: a Phase α session re-scored under the new aggregator must remain within ±2pp per dimension of its original session-level scores.

## Phase 2.7 — TTS gating

- [ ] **2.7.1** [easy] Add `TTS_MODE` env var with default `off`; `adapters/http/voice_runner.py` branches on it.
- [ ] **2.7.2** [easy] Default-off path: WS voice session accepts STT inbound but emits no TTS chunks; UI does not request TTS.
- [ ] **2.7.3** [easy] Update voice tests to cover TTS-off as the default; add a `TTS_MODE=on` path test for parity.
- [ ] **2.7.4** [easy] Update README and bootstrap notes to reflect the new default and the opt-in flag.

## Cross-cutting

- [ ] **X.1** [easy] Update `core/invariants.md` to document all Phase 2 contracts (Problem boundaries, coverage tracker, examiner output schema).
- [ ] **X.2** [easy] After Phase 2 ships, update the auto-memory file `project_interview_system.md` with the new architecture summary.
- [ ] **X.3** [easy] Migration runbook in `docs/`: how to run a session under the Phase 2 path; how to replay legacy α–θ sessions.
- [ ] **X.4** [medium] Chaos-test extension: a mid-problem `SubprocessRuntime` SIGSEGV is recovered cleanly without losing problem boundary state.
- [ ] **X.5** [easy] Lessons log: add `tasks/lessons.md` entry capturing rationale for retiring the 5-stage FSM in favor of examiner-led probing.

---

## Phase 2 totals at a glance

| Sub-phase | Tasks | Easy | Medium | Hard |
|---|---:|---:|---:|---:|
| 2.1 Multi-problem domain | 13 | 5 | 8 | 0 |
| 2.2 Coverage + free-form examiner | 19 | 9 | 9 | 1 |
| 2.3 Problem bank | 11 | 4 | 7 | 0 |
| 2.4 UI continuous-chat | 11 | 6 | 4 | 1 |
| 2.5 Latency | 9 | 1 | 8 | 0 |
| 2.6 Per-problem scoring | 5 | 1 | 3 | 1 |
| 2.7 TTS gating | 4 | 4 | 0 | 0 |
| Cross-cutting | 5 | 4 | 1 | 0 |
| **Total** | **77** | **34** | **40** | **3** |

Hard tasks (3): **2.2.10** examiner emits probe-or-close with streamed JSON, **2.4.11** end-to-end browser test across mode-switch, **2.6.4** business-acumen dimension decision.

These three are the highest-risk items in the build; expect to spike each with a short ADR before committing the implementation.
