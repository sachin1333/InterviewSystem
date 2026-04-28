# Current Task — Phase 2.6 Score Breakdown + Phase 2.7 TTS Gating

## Design
- **Phase 2.6 approach:** Keep the existing session-level `ScoreComputed` unchanged. Add an additive `PerProblemScoreComputed` event emitted during aggregation by grouping scored signals to problem brackets via artifact→turn→problem replay. Surface those events through `ScoreStore.get_problem_scores()` and render them on the existing result page as the recruiter dashboard slice.
- **Phase 2.6 scope:** Complete 2.6.1–2.6.3 with persisted per-problem events and result-page bars. Document the business-acumen decision for 2.6.4 using ten representative rubric scenarios. Add a pinned legacy regression test for 2.6.5 proving legacy stage-only sessions still produce identical session-level aggregate scores.
- **Phase 2.7 approach:** Add `tts_mode` to `VoiceRunner` and `TTS_MODE` env wiring. Default `off` keeps STT + LLM text response and event-log writes but yields no audio chunks and no `AudioChunkAttached`; `on` preserves current TTS behavior.

## Plan
- [x] Write failing tests for per-problem score events, score projection, result dashboard rendering, and legacy aggregate stability.
- [x] Implement additive per-problem event schema and score projection.
- [x] Group signals by problem bracket and emit per-problem scores during aggregation.
- [x] Render per-problem breakdown on `result.html`.
- [x] Document the 2.6.4 business-acumen decision.
- [x] Write failing tests for default-off TTS and opt-in TTS parity.
- [x] Implement `tts_mode` in `VoiceRunner`, app env wiring, docs, and examples.
- [x] Run focused tests, ruff, mypy, full pytest, then update phase checkboxes.
- [x] Commit, push, and verify remote CI.

## Review
- Added `PerProblemScoreComputed` events, projection support via `ScoreStore.get_problem_scores()`, and per-problem signal grouping during aggregation.
- Rendered per-problem score cards with dimension bars on the existing result page.
- Added a pinned legacy aggregate regression proving stage-only sessions retain stable session-level scores and do not emit problem scores.
- Documented the 2.6.4 business-acumen decision in `docs/business-acumen-dimension-decision.md`: keep business judgment under `insight_interp` for Phase 2.
- Added `TTS_MODE` default-off gating through `VoiceRunner`, `create_app()`, `.env.example`, README, and bootstrap notes.
- Updated voice tests so default voice accepts STT and emits no TTS chunks; `TTS_MODE=on` preserves audio/TTS fallback behavior.
- Verification run:
  - `.venv/bin/python -m pytest -q tests/integration/test_per_problem_scores.py tests/unit/test_voice_runner.py tests/integration/test_voice_ws.py tests/unit/test_events.py` -> pass.
  - `.venv/bin/python -m pytest -q` -> pass.
  - `.venv/bin/python -m ruff check .` -> pass.
  - `.venv/bin/python -m mypy core/events.py core/projections.py adapters/http/session_runner.py adapters/http/app.py adapters/http/voice_runner.py tests/integration/test_per_problem_scores.py tests/unit/test_voice_runner.py tests/integration/test_voice_ws.py tests/integration/test_e2e_voice_chaos.py` -> pass.
