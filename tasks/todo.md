# Current Task — Integrate Phase 2.1 + Phase 2.2 on `main`

## Plan
- [x] Establish safe baseline: `main` already contains Phase 2.1 (HEAD) and an accidental merge/cherry-pick state was present.
- [x] Abort the accidental in-progress merge to avoid duplicate `phase0/backbone` history on `main`.
- [x] Cherry-pick only the Phase 2.2 implementation commit from `phase0/backbone` onto `main` without committing immediately.
- [x] Remove generated virtualenv artifacts (`.venv-310`, `.venv-ci2`) from the integration.
- [x] Resolve Phase 2.2 conflicts intentionally against the Phase 2.1 mainline.
- [x] Run focused and full Phase 2.1/2.2 tests; fix lint/type regressions in changed code.
- [x] Commit the clean Phase 2.2 integration on `main` (HEAD).
- [x] Clean safe stale local branches/worktrees; preserve dirty or unmerged branches to avoid losing local history.

## Review
- Accidental merge was aborted; Phase 2.2 was reapplied as a cherry-pick without carrying duplicate `phase0/backbone` history.
- Generated virtualenv artifacts from the Phase 2.2 commit were removed from the integration.
- Conflict resolution kept the Phase 2.2 deterministic case-bank schema (`prompts` with stable SHA-256 selection) over the older Phase 2.1 minimal `cases` schema.
- Verification run:
  - `.venv/bin/python -m ruff check .` → pass.
  - `.venv/bin/python -m pytest -q` → pass.
  - `.venv/bin/python -m mypy adapters/examiner/llm_examiner.py adapters/http/session_runner.py core/case_bank.py core/coverage.py core/domain.py core/events.py core/problem_sequencer.py` → pass.
  - Full `mypy core adapters tests` was also attempted and still fails on pre-existing untyped test issues outside this integration scope.

- Commit created on `main`: `feat(2.2): integrate coverage-driven examiner`.
- Cleaned safe stale artifacts: removed merged/clean branches `claude/reverent-gagarin-89703f`, `claude/thirsty-dijkstra-0d7554`, `claude/thirsty-williams-91838b`; removed clean worktrees `reverent-gagarin-89703f` and detached `eloquent-elbakyan-3ed580`.
- Preserved dirty/unmerged stale artifacts instead of force-deleting: `ci-fixes-and-theta3` worktree has unresolved conflicts; `claude/thirsty-colden-90dc77` worktree has local modifications; `phase0/backbone` and old `claude/*` branches are not ancestors of main even though Phase 2.2 content is superseded by HEAD.
