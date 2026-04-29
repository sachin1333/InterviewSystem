# Current Task — Fix OpenRouter 400 Streaming Failure

## Root-cause investigation plan
- [x] Reproduce provider/router failure with an HTTPError carrying an OpenRouter-style JSON body.
- [x] Confirm streaming failure behavior is handled before the SSE response generator can crash the ASGI task.
- [x] Inspect OpenRouter request construction for invalid defaults and error-message loss.

## Implementation plan
- [x] Add focused tests for OpenRouter HTTP error detail extraction and safe streaming fallback.
- [x] Implement minimal provider-side HTTPError handling that preserves status/body without leaking secrets.
- [x] Implement minimal router-side deferred streaming recovery so provider errors during token iteration yield deterministic fallback instead of crashing SSE.
- [x] Run focused tests and relevant quality checks.

## Review
### Root cause
- OpenRouter HTTP 400 responses were wrapped as `HTTP Error 400: Bad Request`, discarding the response body that explains which request/model failed.
- The SSE endpoint starts the HTTP 200 response before iterating `ModelRouter.iter_streaming()`, so provider failures raised during streaming escape the generator and surface as ASGI exceptions.
- `OpenRouterRouter.call(stream=True)` returned a generator tied to a response opened inside a `with` block; the response could be closed before iteration.

### Changes made
- Added OpenRouter HTTPError parsing that includes status and a bounded provider error message while avoiding request/header/API-key leakage.
- Moved OpenRouter streaming into a generator that owns the `urlopen()` context for the duration of iteration.
- Hardened `ModelRouter.iter_streaming()` to yield the deterministic placeholder if the provider fails before or during token iteration, allowing SSE responses to terminate cleanly.
- Added regression tests for HTTP error detail extraction and both streaming failure paths.

### Verification
- RED: `rtk uv run pytest tests/unit/test_model_router.py::test_iter_streaming_returns_placeholder_when_provider_call_fails tests/unit/test_model_router.py::test_iter_streaming_returns_placeholder_when_provider_iterator_fails tests/unit/test_openrouter_router.py::test_http_error_includes_openrouter_response_body -q` failed with the original exceptions / missing OpenRouter body.
- GREEN: same focused regression command -> 3 passed.
- Focused suite: `rtk uv run pytest tests/unit/test_model_router.py tests/unit/test_openrouter_router.py tests/integration/test_probe_sse.py -q` -> 7 passed.
- Quality: `rtk uv run ruff check adapters/llm/openrouter_router.py adapters/llm/router.py tests/unit/test_model_router.py tests/unit/test_openrouter_router.py` -> pass; `rtk uv run mypy adapters/llm/openrouter_router.py adapters/llm/router.py tests/unit/test_model_router.py tests/unit/test_openrouter_router.py` -> pass.
- Full suite: `rtk uv run pytest -q` -> pass.

---

# Current Task — Fix RCA Findings: Chat Workflow, Progression, Voice, Latency

## Design
- **Priority:** Restore correct interview progression first, because it is the root cause that also triggers synchronous scoring latency after the first answer.
- **Orchestration approach:** For problem-bank sessions, keep problem boundaries authoritative. While a problem is active, candidate turns trigger examiner probe/close logic; scoring/aggregation can only occur after `ProblemSequencer` reports all planned problems closed. Wire `LlmExaminer` in production.
- **UI approach:** Keep domain events unchanged, but render boundaries as metadata (`Problem N`) and render opener text only in the interviewer bubble. Make form submit behavior explicit: requestSubmit for Enter, disable/reset visible controls after native submission starts, avoid browser restoration.
- **Voice approach:** Keep TTS optional. Add an explicit `examiner_text` WS message so `TTS_MODE=off` still shows the interviewer response. Gate active voice UI on an actual runner, add status UI, and harden PTT startup/close behavior.
- **Latency approach:** Remove premature scoring from the active-problem GET path, route lower-risk model tasks through configured lower tiers, and verify the first-answer path no longer hits scorer code.

## Plan
- [x] Add failing regression tests for problem progression, duplicate opener, input reset markup/JS, voice text message/gating, and no premature scoring after first answer.
- [x] Implement problem-bank progression fix in `SessionRunner.advance()` and wire `LlmExaminer` in `create_app()`.
- [x] Implement chat UI fixes in `_conversation_messages()` and `turn.html`.
- [x] Implement voice protocol/UI fixes in `voice_protocol.py`, `voice_runner.py`, `app.py`, `turn.html`, and `voice.js`.
- [x] Implement minimal latency safeguards: prevent premature scoring while a problem is active and wire examiner/communication scorer tiers through `task_tier_map`.
- [x] Run focused tests, JS syntax check, ruff, mypy for touched files, and full pytest.

## Review
### Changes made
- Added regression coverage for Phase-2 problem-bank progression after first answer. The test proves the app advances from problem 1 to problem 2 through `ProblemClosed`/`ProblemIntroduced` without `ScoreComputed`/`SessionEnded` and with an exploding scorer proving scorer code is not called while a problem is active.
- Updated `SessionRunner.advance()` so active problem-bank candidate turns invoke examiner review/close/probe before legacy scoring logic can run. `RequestExecution` remains honored before examiner review.
- Wired `LlmExaminer(router)` into production `create_app()`.
- Changed chat boundaries to display `Problem N` metadata only; opener text now appears once in the interviewer bubble.
- Added composer stale-input protections: `autocomplete="off"`, Enter uses `requestSubmit()`, submit disables send and clears visible fields after native serialization, and `pageshow` resets browser-restored fields.
- Added `examiner_text` voice WS message and `VoiceTurnResult.examiner_text`, so `TTS_MODE=off` still displays the interviewer response.
- Gated active voice UI on `voice_runner` availability, added `#voice-status`, and hardened PTT quick-release/WebSocket-open handling.
- Wired `task_tier_map` into `LlmExaminer` and `BaseLlmScorer`; `examiner.probe` now uses `mid`, and communication/problem-framing use their configured mid tier.

### Subagent work
- Voice fixes were implemented by a worker subagent with disjoint ownership of voice protocol/UI/tests.
- Worker RED: five new voice tests failed for missing behavior.
- Worker GREEN: `rtk uv run pytest -q tests/integration/test_voice_ws.py tests/integration/test_voice_ui_ptt.py` -> 12 passed; `rtk node --check adapters/http/static/voice.js` -> exit 0.

### Verification
- Focused regression suite: `.venv/bin/python -m pytest -q tests/integration/test_problem_bank_session.py tests/integration/test_continuous_chat_ui.py tests/integration/test_text_socratic_flow.py tests/integration/test_http_happy.py tests/integration/test_voice_ws.py tests/integration/test_voice_ui_ptt.py tests/integration/test_mode_switch_bidirectional.py tests/unit/test_examiner.py tests/unit/test_scorers.py tests/unit/test_invariants.py tests/unit/test_problem_domain.py` -> pass.
- JS syntax + ruff: `node --check adapters/http/static/voice.js` and `ruff check ...` -> pass.
- Mypy touched files/tests: `mypy adapters/http/app.py adapters/http/session_runner.py adapters/http/voice_protocol.py adapters/http/voice_runner.py adapters/examiner/llm_examiner.py adapters/scorer/_base.py adapters/llm/task_tier_map.py tests/integration/test_problem_bank_session.py tests/integration/test_continuous_chat_ui.py tests/integration/test_voice_ws.py tests/integration/test_voice_ui_ptt.py tests/integration/test_mode_switch_bidirectional.py tests/unit/test_examiner.py tests/unit/test_scorers.py` -> pass.
- Full test suite: `.venv/bin/python -m pytest -q` -> pass.
