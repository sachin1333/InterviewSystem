# Current Task — Phase 2.5 Latency Instrumentation Slice

## Plan
- [x] Confirm Phase 2.4 landed on `main` and remote CI passed before starting additional work.
- [x] Inspect existing latency/event primitives and identify the smallest Phase 2.5 slice that improves observability without changing interview semantics.
- [x] Write failing tests for per-turn latency instrumentation on candidate submit and examiner/challenger response generation.
- [x] Add event schema/projection support only as needed for timing dashboard inputs.
- [x] Instrument HTTP/session-runner hot paths with monotonic timing fields (`submit_received_ms`, `context_assembled_ms`, `first_token_ms`, `first_paint_ms`) and safe defaults.
- [x] Run focused latency tests, lint, type checks, and full pytest.
- [x] Update `tasks/phase2_tasks.md` for completed Phase 2.5 scope.
- [x] Commit and push the verified Phase 2.5 slice.

## Review
- Phase 2.4 remote CI: GitHub Actions run `25059296189` passed on `main` at commit `dd759d5`.

## Phase 2.5 Slice Review
- Added `TurnTimingObserved` event schema for server-side turn checkpoints: `submit_received_ms`, `context_assembled_ms`, `first_token_ms`, and `first_paint_ms`.
- Instrumented problem opener, examiner probe, and candidate submit paths with monotonic timings and persisted event-log entries.
- Added regression coverage for event registry/schema and timing emission on opener generation and candidate submit.
- Marked only Phase 2.5.6 complete; streaming, prefetch, dashboard, load-test, and fallback items remain open.
- Verification run:
  - `.venv/bin/python -m pytest -q tests/integration/test_turn_timing_events.py tests/integration/test_continuous_chat_ui.py tests/integration/test_problem_bank_session.py tests/integration/test_first_paint_latency.py` -> pass.
  - `.venv/bin/python -m ruff check .` -> pass.
  - `.venv/bin/python -m mypy core/events.py adapters/http/session_runner.py adapters/http/app.py tests/integration/test_turn_timing_events.py tests/unit/test_events.py` -> pass.
  - `.venv/bin/python -m pytest -q` -> pass.
