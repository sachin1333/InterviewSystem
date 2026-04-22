# Lessons — InterviewSystem

Running log of self-correction patterns. Append after every user-triggered correction.

## 2026-04-21 · Architecture review pass

- **Ambiguity first, always.** User asked "review and validate the system design and plan." Multiple valid interpretations existed (gap vs quality, one doc vs two, report vs redline). Used AskUserQuestion before any work. Pattern: whenever a deliverable word (review/validate/plan/design) is modified by "the", surface interpretations before doing work.
- **Duplicate row on first edit.** Inserted a new MEMORY.md row in §11.2 before removing the existing row → table had two MEMORY.md entries. Fix: when adding a row to an existing table, grep the table first for the same key, then decide add-vs-edit. One pass, not two.
- **Phase renumbering has long tail.** Inserted new Phase 2 → had to update §11.7, §12.7, §13.7 roadmap-impact cross-refs. `Grep "Phase [0-9]"` after any phase-order change to catch orphan references. Did this eventually but not proactively.
- **Independent verification catches false-low.** My own review ranked event-log seq concurrency as LOW; code-reviewer pushed back that for an append-only audit log, silent corruption → MEDIUM. Pattern: any data-integrity issue in an audit-critical system defaults to MEDIUM minimum, even if probability is low.
- **Missed FSM contract gap on first pass.** The doc says orchestrator is pure + `next(state, rubric) → Action`, but never wrote the state-transition table. I flagged "contract conformance tests undefined" at LOW but missed that the orchestrator's *own* contract is undefined. Pattern: for any "pure function" claim, require a written invariants table — without it, conformance tests are vacuous.
