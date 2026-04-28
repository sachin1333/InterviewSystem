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
