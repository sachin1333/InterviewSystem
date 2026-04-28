# Orchestrator FSM Invariants

> **Phase 2.1 status:** Problem-based FSM is live. Stage events (`StageEntered` /
> `StageCompleted`) are **legacy / replay-only** — they are still emitted for
> analytics on old sessions but the hot-path FSM no longer branches on them.
> See the _Problem-boundary contract_ section below for the new invariants.

Pure function contract: `next_action(session_id, sessions, scores, policy) -> Action`.

**Zero I/O.** Table-driven. Given projection snapshot, deterministic single `Action` returned.

## Inputs

- `SessionStore` snapshot — per-session derived state (turn ids, artifact ids, started_at, ended, target_answers, max_probes_per_prompt, prompt_turn_ids, answer_turn_ids, probe_turn_ids, last_turn_actor, last_turn_kind, last_turn_id, turn_to_artifacts).
- `ScoreStore` snapshot — composite `Score` if aggregator ran.
- `SignalStore` snapshot — per `(session, dimension)`: signal present OR scorer failed OR missing.
- `RuntimeStore` snapshot — per turn: executed / failed / not-yet-run.
- `policy` — read from `SessionStore` record (set at `SessionStarted`): `target_answers`, `max_probes_per_prompt`, `scored_dimensions`.

## Projection state classification

Derived, not stored. Computed per call from snapshots:

| Flag | Meaning |
|---|---|
| `unstarted` | no session record |
| `no_prompt` | session started, zero `TurnPosted(actor=challenger, kind=question)` events |
| `awaiting_answer` | last challenger/examiner turn has no following candidate `answer`/`defense` turn |
| `answer_has_code` | last answer attached an `Artifact(kind=code_cell)` with no matching `RuntimeExecuted`/`RuntimeFailed` |
| `answer_awaits_probe_decision` | last candidate turn done, examiner has not emitted probe-or-advance |
| `probe_pending_answer` | examiner emitted probe turn, no candidate follow-up yet |
| `target_reached` | `answer_count >= policy.target_answers` |
| `has_unscored_artifacts` | scorable artifacts without per-dim `Signal` |
| `signals_incomplete` | at least one dimension in `policy.scored_dimensions` has zero signals (neither real nor failure) |
| `composite_missing` | all scorable dims have signals OR explicit `ScorerFailed`, but no `ScoreComputed` |
| `scored` | `ScoreComputed` event present |
| `ended` | `SessionEnded` present |

Precedence: topmost matching → dispatched action.

## State-transition table

| # | Condition | Action | Notes |
|---|---|---|---|
| 1 | `unstarted` | `NoAction(reason="unknown-session")` | defensive |
| 2 | `ended` | `NoAction(reason="session-ended")` | terminal |
| 3 | `scored` AND NOT `ended` | `EndSession(session_id, reason="scored")` | |
| 4 | `no_prompt` | `RequestChallenge(session_id)` | challenger drives first prompt |
| 5 | `answer_has_code` | `RequestExecution(session_id, artifact_id=<latest code cell>)` | sandbox runs code |
| 6 | `probe_pending_answer` | `RequestCandidateInput(session_id, kind=defense)` | candidate defends |
| 7 | `awaiting_answer` | `RequestCandidateInput(session_id, kind=answer)` | first answer to prompt |
| 8 | `answer_awaits_probe_decision` AND `probe_count < max_probes_per_prompt` | `RequestProbe(session_id)` | probes are per-prompt; budget drains independently of `target_reached` so the last prompt still gets its probes |
| 9 | `answer_awaits_probe_decision` AND `probe_count >= max_probes_per_prompt` | `RequestChallenge(session_id)` OR `RequestScoring...` chain | advance: if `target_reached` → start scoring; else new prompt |
| 10 | `has_unscored_artifacts` AND `signals_incomplete` | `RequestScoring(session_id, artifact_id, dimension)` for each missing (dim, artifact) pair | fan-out; orchestrator returns the single highest-priority dispatch, caller loops |
| 11 | `signals_incomplete` AND NOT `has_unscored_artifacts` | record `ScorerFailed(dimension, reason="no-scorable-artifact")` then mark dim insufficient | handled by caller |
| 12 | `composite_missing` | `RequestAggregate(session_id)` | aggregator runs |
| 13 | default | `NoAction(reason="nothing-to-do")` | |

## Forbidden transitions (invariants the log must preserve)

- `TurnPosted(actor=candidate)` never appears before a `TurnPosted(actor=challenger, kind=question)` OR a preceding `TurnPosted(actor=examiner, kind=probe)`. Violates row 7 precondition.
- `ScoreComputed` never appears with all-dim signals absent. Guarded by row 12 requiring signals.
- Two `SessionStarted` per session. Log must reject.
- `ArtifactAttached.produced_by_turn_id` must reference an already-logged `TurnPosted.id`. Referential.
- `seq` monotonically increasing per session. Guarded by EventLog append.

## Idempotency contract

- `RequestScoring(a, d)` emitted multiple times while scorer in-flight is safe: scorer dedupes by `(artifact_id, dimension)` key when emitting `Signal` or `ScorerFailed`.
- `RequestChallenge` / `RequestProbe` emitted multiple times: Challenger/Examiner adapters dedupe by session+last-turn-seq.
- Orchestrator is memoryless across calls — caller is responsible for not double-dispatching if an action is in flight. Recommended pattern: caller records `TurnRequested(of=adapter, kind=...)` on dispatch; row 4/8/9 condition tightens to "no pending TurnRequested".

## Test obligations

Every row 1-13 has a `tests/unit/test_invariants.py::test_row_N` that:
1. Builds synthetic projections matching that row's condition.
2. Asserts `next_action()` returns the exact action shape.
3. Asserts `next_action()` is pure (calling twice returns equal result).

Forbidden-transition tests live in `tests/unit/test_eventlog.py` and `tests/unit/test_events.py`.

---

## Problem-boundary contract (Phase 2.1)

`ProblemSequencer` drives problem boundaries. Pure function; no I/O.

### New event types

| Event | Fields | Meaning |
|---|---|---|
| `ProblemIntroduced` | `problem_id`, `opener_text`, `ordinal` | Examiner presents new problem; challenger posts opener text as a `TurnPosted(challenger, question)` in the same step |
| `ProblemClosed` | `problem_id`, `reason`, `rationale` | Examiner declares problem done; `reason` ∈ `{coverage_saturated, time_capped, examiner_pivot, max_probes}` |

### Ordering invariants

1. **Introduce before turn** — `ProblemIntroduced(N)` must precede any `TurnPosted` for that problem.
2. **Close before next introduce** — `ProblemClosed(N)` must precede `ProblemIntroduced(N+1)`.
3. **End after last close** — `SessionEnded` follows the last `ProblemClosed`; never fires while a problem is active.
4. **No double-introduce** — `ProblemIntroduced` for an already-introduced `problem_id` is rejected by `ProblemSequencer.validate_introduce`.
5. **No double-close** — `ProblemClosed` for an already-closed or never-introduced `problem_id` is rejected by `ProblemSequencer.validate_close`.

### Projection state

`SessionStore` now tracks per-session:
- `problems: list[Problem]` — problems introduced so far (append-only, idempotent).
- `current_problem_id: ProblemId | None` — set on `ProblemIntroduced`, cleared on `ProblemClosed`.
- `problem_status: dict[ProblemId, _ProblemStatus]` — `active` or `closed`.

### Legacy / backward-compat

- Sessions created before Phase 2.1 (Phases α–θ) have no `ProblemIntroduced`/`ProblemClosed` events. Their `problems`, `current_problem_id`, and `problem_status` fields are empty/`None`. They replay cleanly.
- `StageEntered` / `StageCompleted` are still registered in `EVENT_TYPES` and survive SQLite round-trips. `SessionRunner` only executes the legacy stage branch when `problems` list is empty.

### Sequencer action table

| Condition | Action |
|---|---|
| `problems` list empty | `Noop(reason="no_problems_planned")` |
| Active problem in flight (`current_problem_id` non-null, status `active`) | `Noop(reason="problem_active")` |
| Next un-introduced problem exists | `IntroduceNext(problem, ordinal)` |
| All planned problems closed | `EndSession` |
| All introduced but some not yet closed | `Noop(reason="awaiting_close")` |

### Test obligations

`tests/unit/test_problem_domain.py` covers:
- `Problem` dataclass construction and defaults.
- `ProblemIntroduced` / `ProblemClosed` field validation.
- `SessionStore` projection for single-problem, multi-problem, and idempotent replay scenarios.
- All five `ProblemSequencer` action paths.
- All `validate_introduce` / `validate_close` error cases.
- Legacy Phase α session replay (stage events only) — projection correct, no errors.
- SQLite round-trip with mixed legacy + Phase 2.1 events.
