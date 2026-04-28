# Phase 2 — Continuous-Chat, Multi-Case, Adaptive Interview

**Status:** Spec, awaiting plan-mode approval.
**Author:** Reframed 2026-04-28 from user requirements + gap analysis against Phase α–θ.
**Supersedes:** the rigid 5-stage Socratic FSM scaffolding from Phases ε–η. Builds on (does not rebuild) the event-sourced log, ModelRouter, scorers, rubric, voice infra, and SSE streaming already shipped.

---

## 1. Vision

A candidate joins a single chat session. The session contains **several problem statements**, presented one at a time as the conversation progresses. Each problem statement opens with a **narrow first question** — not the full problem dumped at once. The candidate answers (by voice or by typing). The examiner reads that answer and generates the **next question on the fly**, probing assumptions, gaps, leaps, or pushing the candidate to defend a choice. When the examiner has enough signal across the rubric for that problem, it closes that problem and introduces the next one. Repeat until the planned problem set is exhausted.

The chat shows questions as **text only**. Answers come back as **streamed voice (STT) or typed text** — candidate's choice, switchable mid-session. The candidate has unlimited thinking time per turn; the system is held to a hard **p50 ≤ 800ms / p95 ≤ 1500ms** turn-to-question latency budget. Evaluation uses the existing six-dimension rubric — no rubric change in Phase 2.

---

## 2. What changes vs. current build

| Concern | Today (Phases α–θ) | Phase 2 |
|---|---|---|
| Session shape | One case per session, 5 hard-coded stages | One session, **N problem statements** sequenced by the examiner, no stage boundaries |
| Question flow | Stage→primitive→prompt seed; LLM only fills probes inside a fixed scaffold | Examiner is **the driver**: chooses what to ask next from transcript + coverage state |
| First question per problem | Whole case prompt loaded from CaseBank | **Narrow opener** for the problem; full context revealed only as probes demand it |
| Voice/text | Voice-first, text fallback exists but second-class | Text and voice are **peers**; toggle exposed in UI; TTS off by default |
| TTS | Cartesia Sonic primary, ElevenLabs fallback (always on if voice mode) | **Off by default**, kept behind a flag for future research; questions render as text |
| Scoring | 6 dimensions, session-level aggregate | Same 6 dimensions, plus **per-problem score breakdown** alongside session aggregate |
| Latency | First-paint cached; examiner probes stream tokens | Same plus **prefetched probe context** during candidate think-time, and streaming on every challenger turn (not just probes) |

---

## 3. What is removed

These exist in the codebase and must be retired from the runtime hot path. Schema/data may stay for replay; FSM control flow does not use them.

- `CaseDefinition.stages` — the linear 5-stage chain in `core/case_loader.py`. Stages no longer drive control flow.
- `StageEntered` / `StageCompleted` event handling in `adapters/http/session_runner.py` — kept as event records (cheap, useful for analytics) but the FSM stops branching on them.
- Hard-coded primitive→stage mapping in `core/primitives.py` consumers. Primitives become **optional metadata** an examiner may attach to a probe; they no longer constrain what is asked.
- Default-on TTS path in `adapters/http/voice_runner.py`. TTS gated behind explicit `TTS_MODE=on` env. Default is text questions only.

Net code deletion is small (≈ a few hundred lines + tests). Most of the change is **disconnecting**, not removing, components.

## 4. What is kept (verbatim, do not rebuild)

- Event-sourced SQLite append-only log as source of truth.
- `ModelRouter` (with `iter_streaming`) and `LlmExaminer.iter_review` — already streams tokens over SSE.
- Wispr Flow streaming STT for voice answers.
- Cold-start `CaseBank` — repurposed as **per-problem opener cache**, not full-case loader.
- Four `Scorer`s + `Aggregator` and the `ds-ml-engineer-v1.yaml` rubric (problem_framing 18%, model_rationale 22%, experiment_design 15%, insight_interp 15%, communication 18%, response_authenticity 12%).
- `response_authenticity` dimension — still our defense against AI-coached candidates.
- SSE token streaming pipeline (`adapters/http/sse_routes.py`) — extended, not replaced.
- FastAPI app shape, session bootstrap, chaos test harness, FSM invariants doc.

## 5. What is new

### 5.1 Multi-case session model
- New first-class concept: a session contains an ordered list of `Problem` records (renamed from "Case" to avoid confusion with the existing `CaseDefinition` legacy type).
- New events: `ProblemIntroduced(problem_id, opener_text)`, `ProblemClosed(problem_id, reason)`. Reasons: `coverage_saturated`, `time_capped`, `examiner_pivot`.
- Session terminates on `SessionEnded`, which now fires after the last `ProblemClosed`, not after a stage chain.

### 5.2 Free-form examiner (LLM-led probing)
- Examiner input: full transcript of the active problem, running coverage state per rubric dimension, list of dimensions still under-served, and the problem's narrow opener.
- Examiner output: either the **next probe text** or a `close_problem` signal with a one-line rationale.
- Selection is the examiner's: it decides when to push, when to pivot, when to close. No external state machine arbitrates this.
- Probe generation honors a **coverage tracker** that gently biases probes toward dimensions still below signal threshold for this problem.

### 5.3 Coverage tracker
- New module: `core/coverage.py`.
- Per active problem, maintains `Dict[Dimension, float]` of accumulated signal strength. Signal increments are emitted by scorers after each candidate turn (cheap, partial-credit allowed).
- Examiner reads this state when generating its next probe and when deciding whether to close.
- Threshold and target dimension list per problem live in the new problem-bank YAML (see 5.6).

### 5.4 Continuous-chat UI
- Single thread of question/answer bubbles, newest at bottom, full history scrollable.
- Text input always visible; mic button toggles to voice mode for the next answer only (or until the candidate toggles back).
- During system think-time: a "thinking…" affordance with the streaming probe text painting in as tokens arrive.
- No "stages", "scenes", or progress bar over hard stage boundaries — replaced with a soft "Problem 2 of 4" header that updates on `ProblemIntroduced`.
- Built on the existing FastAPI + Jinja + EventSource bubble from θ.2; this is incremental, not a rewrite.

### 5.5 Latency machinery additions
- Extend SSE streaming to **challenger** turns (problem openers and any examiner-driven new problem introduction), not only examiner probes.
- New: **probe-context prefetch.** While the candidate is mid-answer (voice transcript still streaming, or text field still open), examiner-side context (transcript summarization, coverage diff) is built in the background. When the candidate submits, only the model call remains.
- Budget contract: p50 ≤ 800ms, p95 ≤ 1500ms from candidate-submit to first-token-of-next-question.

### 5.6 Problem bank
- New file: `templates/problem_banks/ds-ml-engineer-v1.yaml`.
- Each entry contains: opener text (the narrow first question), full problem context (revealed to the examiner only, never directly to the candidate), targeted rubric dimensions and per-dimension thresholds, expected duration window.
- Replaces the role of `CaseBank` for first-prompt rendering; CaseBank stays for backward-compat replay of old sessions.

---

## 6. Latency budget (per turn)

| Stage | Budget | How it's hit |
|---|---|---|
| Candidate submit → server received | ≤ 50ms | unchanged |
| Server received → context assembled | ≤ 100ms | prefetch during candidate think-time (5.5) |
| Context → first token | ≤ 500ms (p50) / ≤ 1100ms (p95) | iter_streaming + warm router |
| First token → text rendering in UI | ≤ 50ms | existing SSE pipeline |
| **Total p50 / p95** | **≤ 800ms / 1500ms** | enforced via timing dashboard |

---

## 7. Evaluation (unchanged)

The six rubric dimensions and weights from `templates/rubrics/ds-ml-engineer-v1.yaml` are preserved exactly. Per the gap analysis, "business acumen" from the original project instructions does not have a dedicated dimension today; `insight_interp` is closest. **Decision deferred** to Phase 2.6 — ship Phase 2 on the existing rubric, evaluate empirically whether `insight_interp` covers business acumen signal, then either rename, split, or extend.

New deliverable: per-problem score breakdown in the recruiter dashboard, alongside the existing session aggregate. No new scorers; same scorers run per-problem and aggregated.

---

## 8. Acceptance criteria

A feature is not done until each of these is demonstrably true.

1. **Multi-problem session.** A session can run end-to-end with at least three distinct problem statements, each closed by `coverage_saturated` rather than by a hard time cap, and the SQLite log shows `ProblemIntroduced` / `ProblemClosed` events in correct order.
2. **Adaptive probing.** A regression test feeds two different candidate answers to the same opener and asserts the next probe text differs and references content from the candidate's specific answer. (No template-only flow.)
3. **Coverage-driven close.** A test simulates a candidate who saturates `problem_framing` and `communication` early; the examiner pivots to under-covered dimensions before closing.
4. **Mode toggle.** A candidate can begin answering by voice, switch to text mid-answer (or for the next answer), and the transcript stays consistent in the event log.
5. **Latency.** A timing dashboard run against a 20-turn synthetic session reports p50 ≤ 800ms, p95 ≤ 1500ms, broken down by the table in §6.
6. **Scoring stability.** A pinned regression session run through both Phase α scorers and Phase 2 multi-problem aggregator produces session-level scores within ±2 percentage points per dimension.
7. **No vestigial paths in the hot loop.** A static check confirms the runtime FSM does not branch on `CaseStage` boundaries; replay analytics may still read them.

---

## 9. Phased build plan

Each sub-phase is independently testable and shippable. Order matters: 2.1 unblocks everything else.

- **Phase 2.1 — Multi-problem domain model.** New `Problem` record, new events, new orchestrator branch. Disconnect `StageEntered/Completed` from FSM control flow. Expose problem boundaries to UI. (≈ medium)
- **Phase 2.2 — Coverage tracker + free-form examiner.** New `core/coverage.py`. Rewrite examiner prompt template (`templates/agents/examiner/`) to take coverage diff + transcript and emit either a probe or `close_problem`. Delete primitive→stage coupling from examiner path. (≈ medium-large; this is the heart of Phase 2)
- **Phase 2.3 — Problem bank.** Author `templates/problem_banks/ds-ml-engineer-v1.yaml` with at minimum 4 problems covering different rubric emphases. Migrate `CaseBank` first-prompt path to read from problem bank for new sessions. (≈ small-medium, gated on content authoring)
- **Phase 2.4 — UI continuous-chat + mode toggle.** Replace the stage-progress chrome with the soft "Problem N of M" header and a single chat thread. Wire mic toggle. Reuse θ.2 EventSource bubble. (≈ medium)
- **Phase 2.5 — Latency: challenger streaming + probe-context prefetch.** Extend `iter_streaming` to challenger; add background prefetch hook keyed off candidate-typing or voice-VAD events. (≈ medium)
- **Phase 2.6 — Evaluation: per-problem breakdown + dashboard.** Aggregator emits per-problem scores; dashboard renders them. Decide on business-acumen dimension treatment. (≈ small-medium)
- **Phase 2.7 — TTS gating.** Default TTS off; flag-gated `TTS_MODE=on` for opt-in. (≈ small, mostly config)

Order of attack: 2.1 → 2.2 → 2.3 → 2.4 in parallel with 2.5 → 2.6 → 2.7.

---

## 10. Open questions parked, not blockers

- Whether to expose problem count and progress to the candidate, or keep it ambient.
- Whether the examiner should ever introduce an unscheduled problem ("looks like you're strong here, let me push you somewhere harder") — interesting, but adds nondeterminism that complicates fairness.
- Whether `response_authenticity` should run continuously (current) or per-problem (new). Default to continuous; revisit if it produces noisy per-problem scores.
- Business-acumen dimension treatment (Phase 2.6 decision point).

---

## 11. Out of scope for Phase 2

- New rubric dimensions (decision deferred).
- Recruiter-side dashboard redesign beyond per-problem breakdown.
- Proctoring changes.
- ATS / HRIS integration changes.
- Any candidate-self-evaluation flow.
