# Lessons — InterviewSystem

Running log of self-correction patterns. Append after every user-triggered correction.

## 2026-04-21 · Architecture review pass

- **Ambiguity first, always.** User asked "review and validate the system design and plan." Multiple valid interpretations existed (gap vs quality, one doc vs two, report vs redline). Used AskUserQuestion before any work. Pattern: whenever a deliverable word (review/validate/plan/design) is modified by "the", surface interpretations before doing work.
- **Duplicate row on first edit.** Inserted a new MEMORY.md row in §11.2 before removing the existing row → table had two MEMORY.md entries. Fix: when adding a row to an existing table, grep the table first for the same key, then decide add-vs-edit. One pass, not two.
- **Phase renumbering has long tail.** Inserted new Phase 2 → had to update §11.7, §12.7, §13.7 roadmap-impact cross-refs. `Grep "Phase [0-9]"` after any phase-order change to catch orphan references. Did this eventually but not proactively.
- **Independent verification catches false-low.** My own review ranked event-log seq concurrency as LOW; code-reviewer pushed back that for an append-only audit log, silent corruption → MEDIUM. Pattern: any data-integrity issue in an audit-critical system defaults to MEDIUM minimum, even if probability is low.
- **Missed FSM contract gap on first pass.** The doc says orchestrator is pure + `next(state, rubric) → Action`, but never wrote the state-transition table. I flagged "contract conformance tests undefined" at LOW but missed that the orchestrator's *own* contract is undefined. Pattern: for any "pure function" claim, require a written invariants table — without it, conformance tests are vacuous.

## 2026-04-22 · Phase α FSM row-8 semantics

- **Row 8 over-gated on `target_reached`.** Wrote invariants row 8 as "probe only if target not reached" then asserted the opposite in the prove-it golden sequence (target=1, still expected a probe after the lone answer). Pattern: when a policy knob (target_answers) and a per-scope budget (max_probes_per_prompt) interact, write the prove-it test first with explicit per-scope expectations, then reverse-engineer the row conditions. Discovered only when tests hit the FSM — the invariants doc *and* the orchestrator both disagreed with the intended interview semantics.
- **Verification-gap exposed by running tests.** Self-marked Phase α as "probably green" before invoking pytest. The first real pytest run caught it immediately. Pattern: "probably green" is not a status — run the suite before any completion claim, per CLAUDE.md §4 and superpowers:verification-before-completion.
- **Sandbox venv symlinks to host python.** `.venv/bin/python` pointed at `/opt/homebrew/opt/python@3.14/...` which only exists on the user's Mac, not the sandbox. Always verify the interpreter resolves in the current environment before running tests; fall back to `uv python install <ver>` + fresh venv when the checked-in venv is host-bound.

## 2026-04-22 · Phase β/γ verification setup

- **Editable installs can fail on flat-layout repos.** `pip install -e '.[dev]'` failed because setuptools auto-discovered multiple top-level packages (`core`, `adapters`, `templates`, `outputs`). Pattern: when a repo is intentionally flat-layout and not yet packaged, do not assume editable install works; either declare packages explicitly in `pyproject.toml` or install the concrete dev dependencies into the verification venv.

## 2026-04-22 · Phase δ adapter/core contract drift

- **When the roadmap outgrows a thin core protocol, widen at the adapter edge first.** Phase δ wanted richer examiner/scorer outcomes (probe-or-advance, signal+failure bundles), but the existing core Protocols were intentionally skinny. Pattern: avoid prematurely widening core contracts unless orchestration truly needs it; introduce adapter-side result objects first, then promote them into core only when multiple call sites demand the richer interface.
