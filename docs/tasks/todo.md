# Current Task — RCA: Chat-Based Interview Workflow End-to-End

**Date:** 2026-04-29
**Scope:** Investigation and recommendations only. Do not implement code changes.

## Investigation plan
- [x] Map the end-to-end chat interview flow from candidate start through final result.
- [x] Trace durable state transitions and event-log writes across HTTP routes, session runner, domain events, orchestrator, examiner/challenger/scorers, runtime, and UI templates.
- [x] Reproduce or inspect tests covering the critical chat path, including problem-bank progression, first answer, follow-up, code execution, completion, and result generation.
- [x] Identify root causes, not symptoms, with file/line evidence and downstream impact.
- [x] Propose solutions and verification strategy without implementing them.

## Review

### Workflow map
1. `POST /sessions` writes `SessionStarted` and `CandidateJoined`, then redirects to `GET /sessions/{id}`.
2. `GET /sessions/{id}` calls `SessionRunner.advance()`, which mutates the event log by introducing problems/challenges, probing, executing code, scoring, aggregating, or ending.
3. `POST /sessions/{id}/turn` writes candidate `TurnPosted`, optional markdown/code artifacts, and timing, then redirects to `GET /sessions/{id}`.
4. `SessionRunner.advance()` replays state, lets `ProblemSequencer` introduce/close problems, delegates to the pure orchestrator for legacy actions, and calls adapters for examiner/scoring/runtime side effects.
5. `turn.html` renders event-log-derived chat messages and optionally opens `/probe-stream` when an examiner probe has no artifact.
6. `GET /sessions/{id}/result` replays `ScoreComputed` / `PerProblemScoreComputed` and renders feedback.

### RCA findings
- **P0:** `POST /turn` accepts writes for ended or unknown sessions. Evidence: route writes candidate turns without replay-state guards before append. Impact: event-log invariants can be violated (`SessionEnded` is not terminal), and arbitrary session IDs can acquire candidate turns without `SessionStarted` / `CandidateJoined`.
- **P0:** Candidate turn submission is a non-atomic multi-event write and ignores the envelope returned by idempotent append. Evidence: a retry after `TurnPosted` succeeds but before artifacts are written can attach artifacts to a newly generated, non-existent turn ID. Impact: candidate answers become orphaned/invisible and the FSM can wedge.
- **P0:** Side-effecting `GET /sessions/{id}` is not same-session concurrency safe. Evidence: concurrent GETs can both advance from the same snapshot and append duplicate challenger turns. Impact: refresh/prefetch/double-open can duplicate interviewer messages or throw append conflicts.
- **P1:** Empty submissions create candidate turns with no candidate artifact. Impact: answer count advances while no scorable evidence exists, producing a no-op/wedged session or invisible candidate turn.
- **P1:** SSE probe streaming is not event-sourced. Evidence: `/probe-stream` calls `examiner.iter_review()` and streams bytes without appending an `ArtifactAttached`; `LlmExaminer.iter_review()` also discards recent turns and coverage. Impact: streamed interviewer text is lost on reload and can contradict the persisted examiner decision.
- **P1:** Examiner failure path posts an empty probe turn. Evidence: failure outcome has `ok_to_advance=True` but `action='probe'`; runner then posts a probe with no artifact. Impact: the candidate is asked to answer a follow-up they may never see, often stuck at “Thinking…”.
- **P1:** Coverage-driven problem closure is currently mostly aspirational. Evidence: coverage is rebuilt from `SignalEmitted` events, but chat scoring runs after all problems are closed; signals have no first-class `problem_id`. Impact: examiner coverage context is empty during active problems, so closure depends on LLM judgment or max-probe cap rather than measured rubric coverage.
- **P1:** Production rubric/scorer configuration is incomplete. Evidence: rubric has six dimensions, while `create_app()` scores/configures only four for chat. Impact: final composite is calculated over partial evidence and reports insufficient dimensions for unscored rubric areas.
- **P2:** Problem plans are not durably selected at session start. Evidence: `SessionRunner._planned_problems()` recomputes from the current `ProblemBank` on every advance. Impact: a bank edit/deploy during a session can change remaining problems and progress totals.
- **P2:** Problem-bank context is not injected into examiner prompts. Impact: follow-ups see the opener/transcript but miss hidden interviewer guidance, target dimensions, and expected duration.

### Verification performed
- Corrected an initial wrong-path pytest command and reran focused chat suites successfully: `rtk uv run pytest -q tests/integration/test_problem_bank_session.py tests/integration/test_continuous_chat_ui.py tests/integration/test_http_double_submit.py tests/integration/test_e2e_crash_resume.py` -> 11 passed.
- Full suite: `rtk uv run pytest -q` -> passed (100%).
- One-off debug probes reproduced the uncovered failure modes above without changing implementation code.


### Implementation plan
- [x] Detailed implementation plan written to `docs/superpowers/plans/2026-04-29-chat-workflow-rca-fixes.md`.
- [x] Implementation completed on branch `codex/chat-workflow-rca-fixes`.

### Implementation review
- Added guarded candidate submission: unknown sessions return 404, ended sessions redirect to result without mutation, blank submissions return 400, and idempotent retries reuse the persisted candidate turn ID.
- Added deterministic idempotency keys for system-generated interviewer turns, probes, scores, problem boundaries, and session end events; concurrent GET regression coverage now proves duplicate prompts are not emitted.
- Added `ProblemPlanSelected` so problem-bank sessions persist their selected problem IDs and bank version once.
- Changed probe SSE to stream persisted examiner artifacts only, and added deterministic fallback probe text for examiner failures.
- Added `ProblemCoverageObserved`, heuristic active-problem coverage emission, and richer examiner coverage context with problem guidance, target dimensions, and expected duration.
- Added chat-specific rubric `templates/rubrics/ds-ml-engineer-chat-v1.yaml` and `LlmExperimentDesignScorer`, aligning chat scored dimensions with the loaded chat rubric and excluding voice-only authenticity from chat composites.
- Updated README with chat rubric and idempotency invariants.

### Implementation verification
- RED checks observed for new guard/concurrency/plan/probe/coverage/rubric tests before implementation.
- Focused regression suite: `rtk uv run pytest -q tests/integration/test_chat_turn_submission_guards.py tests/integration/test_chat_advance_idempotency.py tests/integration/test_problem_plan_persistence.py tests/integration/test_probe_persistence.py tests/integration/test_problem_coverage_flow.py tests/integration/test_problem_bank_session.py tests/integration/test_continuous_chat_ui.py tests/integration/test_http_double_submit.py tests/integration/test_e2e_crash_resume.py tests/integration/test_e2e_concurrent.py tests/unit/test_events.py tests/unit/test_problem_bank.py tests/unit/test_examiner.py tests/unit/test_scorers.py tests/unit/test_coverage.py` -> passed.
- Static checks: `rtk uv run ruff check ...` -> pass; `rtk uv run mypy ...` -> pass.
- Full suite: `rtk uv run pytest -q` -> passed.

### Recommended solution direction
- Make candidate submission a guarded command: require existing active session, reject ended sessions, reject empty submissions, and persist turn+artifacts atomically or with a single idempotent command envelope that reuses the stored turn ID on retry.
- Move side-effecting advancement behind an idempotent per-session command/lock, or make `GET` render-only and trigger advancement through explicit POST/background worker with idempotency keys.
- Persist streamed examiner output as the canonical artifact, or remove SSE as a separate model call and stream from the same persisted examiner command.
- Replace empty examiner-failure probes with a deterministic persisted fallback prompt or a problem close with auditable failure reason.
- Introduce first-class problem attribution for signals and a lightweight off-critical-path coverage pipeline before examiner close decisions.
- Align rubric dimensions, scorer registry, and chat/voice modality policy; either score all rubric dimensions or make omitted dimensions explicitly non-applicable.
- Persist the selected problem plan/version at session start and include problem context/targets in examiner coverage prompts.

---

# Plan — Fast Conversational Voice/Text Interview Updates

**Date:** 2026-04-26
**Scope:** Planning only for four candidate-critical improvements: fast problem loading, conversational problem follow-ups, reliable voice/text switching, and push-to-talk voice capture.
**Source docs reviewed:** `../README.md`, `../architecture-and-plan.md`, `../core/invariants.md`, `../templates/session/HEARTBEAT.md`, `../templates/agents/examiner/PACING.md`, `../tasks/todo.md`, `../tasks/code-update-plan.md`, `research/voice-first-interview-2026-04-26.md`.

---

## Project readout from docs

`InterviewSystem` is an AI-powered DS/ML technical interview platform. Its stable core is an event-sourced interview backbone: `Session -> Turn -> Artifact -> Signal -> Score`. A FastAPI HTTP surface sits above a replayable SQLite event log, deterministic projections, and a pure FSM orchestrator. LLM-backed adapters generate the initial case (`Challenger`), conduct follow-up probes (`Examiner`), and score evidence (`Scorers`). Candidate code can run in a sandboxed Python subprocess. The current UI is a chat-style HTTP candidate flow with an opt-in voice path.

The docs already establish the right architectural constraints for this work:

- **Event log is truth; projections are derived.** Candidate-visible behavior must be represented as turns/artifacts/events, not hidden mutable state.
- **Orchestrator stays pure.** Timing, idle, typing, and pacing rules belong in a `Pacer`/heartbeat layer or UI transport, not inside the FSM.
- **Speed is a product constraint.** Candidate-facing first response should be measured and enforced; scorers stay off the critical path.
- **Humanly interaction is explicit.** Examiner turns should stream, use backchannels, avoid interruption, and ask follow-ups conversationally.
- **Voice is live but not production-hardened.** Docs mention feature flags, fake STT/TTS fallback, latency budget, and a voice-first research stack, but current next-up item is still voice adapter hardening.

---

## Problem statement

The next slice should make interview start and interaction feel immediate and natural:

1. Starting an interview must not block on a slow LLM-generated problem statement.
2. After the problem statement, follow-up questions should be asked as conversation, not as static prompt dumps.
3. Candidate switching between voice and text modes must be deterministic and recoverable.
4. In voice mode, the mic should default to push-to-talk: press and hold to record, release to submit/stop.

---

## Recommended approach

### Approach A — Patch current flow in place

Add a shorter Challenger timeout, fix UI event handlers, and tune prompts. Lowest short-term cost, but start latency remains coupled to live problem generation and voice/text state remains likely to drift across routes/reloads.

### Approach B — Session prewarm + interaction controller (recommended)

Split candidate start into a fast visible start path and background preparation. Use a cached/canned-first problem statement if live Challenger misses a tight first-token budget, then persist whichever problem is chosen as the canonical `TurnPosted(challenger, question)`. Add a small client-side interaction controller that owns text/voice mode, push-to-talk state, and transcript submission. Keep durable facts in the event log; keep mic button/typing/partial transcript state ephemeral.

This aligns with the docs: pure FSM remains untouched except where new explicit event/action types are required; the candidate path gets faster; voice/text bugs are isolated to a testable UI state machine.

### Approach C — Full realtime voice architecture now

Move immediately to WebRTC/WebSocket voice-to-voice using the research stack. Best eventual UX, but too broad for this urgent slice because it entangles STT, TTS, model streaming, turn-taking, and provider reliability before the current voice/text basics are proven.

**Decision:** Plan around Approach B, with seams that do not block a later Approach C upgrade.

---

## Target UX and invariants

### Interview start

- Candidate clicks **Start interview** and sees a session shell in under 1 second on normal local/dev conditions.
- UI immediately shows either:
  - a canonical problem statement if precomputed/cached, or
  - a warm loading state with visible interviewer presence and progress, not a blank page.
- Live Challenger generation has a strict deadline. On breach, use a deterministic canned or pre-generated problem and audit the breach.
- Exactly one problem statement becomes canonical in the event log.

### Conversational follow-ups

- The problem statement is the Challenger's only large setup turn.
- Follow-up questions are Examiner turns: short, contextual, one question at a time, streamed where possible.
- Backchannels are rendered inline or lightly, not as large prompt cards.
- Examiner should reference the candidate's latest answer and ask about assumptions, tradeoffs, counterfactuals, or clarification.

### Voice/text mode switching

- Mode is explicit: `text`, `voice_idle`, `voice_recording`, `voice_processing`, `voice_playing`, `switching`, `error`.
- Candidate can switch modes only from safe states; active recording is stopped/cancelled before switching to text.
- Draft text and recognized transcript are never silently lost.
- If STT/TTS fails, candidate gets a clear recovery path and text mode remains available unless policy is `VOICE_MODE=forced`.

### Push-to-talk mic

- Default voice behavior is press-and-hold.
- Press starts capture after mic permission is available.
- Release stops capture and submits the audio/transcript.
- Pointer cancel, escape, blur, route changes, and permission denial all stop recording safely.
- The app should not continuously listen by default.

---

## Phased delivery plan

### Phase 1 — Baseline and instrumentation

- [ ] Measure current start latency from `POST /sessions` to first problem visible.
- [ ] Measure Challenger wall time and first-token time separately from page/render time.
- [ ] Add browser-side marks for `candidate_start_click`, `session_shell_visible`, `problem_first_visible`, `problem_canonicalized`.
- [ ] Add server-side logs/events for Challenger timeout, fallback usage, and selected problem source.
- [ ] Write regression tests that fail under current slow-loading behavior where possible.

**Acceptance:** We can state p50/p95 for start flow and identify whether delay is LLM, server, template render, or client-side UI.

### Phase 2 — Fast problem statement loading

- [ ] Introduce a `ProblemProvider` boundary around Challenger generation: `live`, `cached`, and `canned` sources.
- [ ] Preload or pre-generate problem candidates before or immediately after candidate join, without blocking shell render.
- [ ] Enforce a candidate-visible deadline: if live generation misses the configured threshold, canonicalize cached/canned fallback.
- [ ] Persist the chosen problem as the single `TurnPosted(actor=challenger, kind=question)` plus an audit event for fallback or timeout.
- [ ] Update UI to show shell/interviewer presence immediately while problem selection finishes.

**Acceptance:** Session shell appears quickly; problem first visible meets the agreed SLA; fallback path is deterministic and auditable; no duplicate problem turns.

### Phase 3 — Conversational question flow after the problem

- [ ] Route post-problem follow-ups through Examiner, not Challenger.
- [ ] Add or tighten Examiner prompt constraints: one conversational question per turn, refer to latest candidate evidence, avoid reprinting the full problem.
- [ ] Use question primitives from the research doc: `think_aloud`, `socratic_rebuttal`, `counterfactual`, `resume_deep_dive`, `verbal_whiteboard`, `one_bullet`.
- [ ] Update heartbeat/pacer rules so backchannel then probe cadence is consistent with `HEARTBEAT.md` and `PACING.md`.
- [ ] Add tests for transcript shape: problem statement once, then short Examiner follow-ups that are tied to candidate answers.

**Acceptance:** A sample session reads like a conversation: setup -> candidate answer -> acknowledgement -> targeted follow-up, with no static multi-question dumps.

### Phase 4 — Reliable voice/text interaction controller

- [ ] Define a single UI state machine for mode and capture lifecycle.
- [ ] Keep ephemeral UI state out of the event log: mic pressed, partial transcript, audio chunks, typing flags.
- [ ] Persist only durable outputs: final candidate answer/defense turn, transcript artifact, audio blob reference if retained by policy.
- [ ] Make mode switching idempotent and cancel-safe: stop active recording/playback before entering text mode.
- [ ] Preserve drafts/transcripts across switching and browser refresh where practical.
- [ ] Add UI tests for switching during idle, recording, STT processing, TTS playback, provider error, and forced voice policy.

**Acceptance:** Switching modes never wedges the UI, loses candidate text silently, or posts duplicate turns.

### Phase 5 — Push-to-talk voice default

- [ ] Change default mic behavior in voice mode to press-and-hold.
- [ ] Support pointer, mouse, touch, and keyboard accessibility paths.
- [ ] On press: request permission if needed, start capture, show recording state.
- [ ] On release: stop capture, finalize transcript, submit or stage it according to current product decision.
- [ ] On cancel/blur/navigation/error: stop capture and show recoverable state.
- [ ] Add tests for press-hold-release, tap-too-short, pointercancel, permission denied, and switch-to-text-while-recording.

**Acceptance:** The mic is never hot unless the candidate is actively holding the control, and release reliably ends capture.

### Phase 6 — End-to-end verification and docs

- [ ] Run unit, integration, and browser smoke tests for text-only, voice-on, and voice-forced modes.
- [ ] Run fake STT/TTS offline tests so CI does not require provider keys.
- [ ] Run provider-backed manual smoke only when keys are available.
- [ ] Update `README.md` voice behavior docs and any candidate-facing copy.
- [ ] Add a review section with measured before/after latency and known limitations.

**Acceptance:** Tests prove the three modes, the start SLA, conversation transcript shape, and push-to-talk lifecycle.

---

## Suggested implementation order

1. Instrument before optimizing.
2. Make start fast with fallback/canonicalization.
3. Fix conversation sequencing.
4. Add the explicit voice/text state machine.
5. Change mic behavior to push-to-talk.
6. Verify all modes and document.

This order reduces risk: start latency is isolated first, conversational behavior is server/prompt/pacer work, then voice UI state is hardened without mixing it with problem-generation latency.

---

## Key risks and mitigations

| Risk | Severity | Mitigation |
|---|---:|---|
| Live Challenger remains slow | High | Strict deadline plus cached/canned fallback; audit chosen source. |
| Duplicate problem statements | High | Canonicalize through one event-log write with idempotency key. |
| Voice/text state bugs | High | Single explicit UI state machine; no scattered button handlers. |
| Browser mic permission edge cases | Medium | Treat permission denial as recoverable and keep text mode available. |
| Candidate transcript loss | High | Preserve drafts and final transcripts; require explicit discard for destructive switches. |
| Provider outage | Medium | Fake/offline adapters in CI; text fallback in product. |

---

## Open product decision before implementation

For push-to-talk release behavior, choose one:

- **Recommended:** release finalizes transcript into the answer box, then candidate presses Send. Safer for interview accuracy and accidental speech.
- **Faster voice flow:** release finalizes and submits immediately. Lower friction, higher accidental-submit risk.

Default plan assumes the recommended safer behavior unless changed.

---

## Review checklist for implementation plan

- [ ] Every durable candidate-visible change maps to an event/turn/artifact.
- [ ] Orchestrator purity is preserved.
- [ ] Candidate-facing latency has measurable p50/p95 before and after.
- [ ] Voice/text transitions are tested as a state machine, not just click handlers.
- [ ] Text fallback remains available when voice providers fail, except where `VOICE_MODE=forced` intentionally hides manual switching.
- [ ] No scorer work is placed on the candidate critical path.
