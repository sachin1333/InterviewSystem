# Current Task — Phase 2.3 Problem Bank

## Plan
- [x] Inspect current app/session runner/case-bank wiring and define the smallest Phase 2.3 integration surface.
- [x] Write failing tests for problem-bank schema validation, deterministic picking, balanced sequence selection, and fairness.
- [x] Implement `core/problem_bank.py` with loader, validation, deterministic 3–4 problem picker, and opener prewarm helper.
- [x] Author `templates/problem_banks/ds-ml-engineer-v1.yaml` with four DS/ML problems covering the requested rubric emphases.
- [x] Write failing integration tests proving new sessions use ProblemBank problems while legacy CaseBank remains usable.
- [x] Wire `adapters/http/app.py` / `SessionRunner` construction to load ProblemBank for new sessions and keep CaseBank for legacy paths.
- [x] Update `tasks/phase2_tasks.md` checkboxes for 2.3 items that are completed by this slice.
- [x] Run focused tests, full pytest, ruff, and targeted mypy on changed modules.
- [x] Commit Phase 2.3 implementation locally.

## Review
- Added `core/problem_bank.py` with strict YAML validation, deterministic per-session sequence selection, tag lookup, and opener prewarm support.
- Authored `templates/problem_banks/ds-ml-engineer-v1.yaml` with four DS/ML problems covering framing/data quality, model rationale, experiment design, insight interpretation, and communication.
- Wired `SessionRunner` to draw a 3-4 problem sequence from `ProblemBank` when explicit `problems` are not injected.
- Wired `create_app()` to load and prewarm the DS/ML problem bank for new sessions while leaving `CaseBank` in place for legacy cold-start/stage paths.
- Added tests for schema validation, deterministic/balanced/fair picking, opener prewarm, and app/session-runner integration.
- Verification run:
  - `.venv/bin/python -m pytest -q` -> pass.
  - `.venv/bin/python -m ruff check .` -> pass.
  - `.venv/bin/python -m mypy core/problem_bank.py adapters/http/session_runner.py adapters/http/app.py tests/unit/test_problem_bank.py tests/integration/test_problem_bank_session.py` -> pass.
