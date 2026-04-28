# Current Task — Phase 2.4 Continuous Chat UI

## Plan
- [x] Review Phase 2.4 requirements and current HTTP/session projection paths.
- [x] Write focused integration tests for problem progress, problem boundary rendering, pending probe streaming affordance, and voice→text partial transcript preservation.
- [x] Implement continuous-chat projection support in `adapters/http/app.py` without changing event-log semantics.
- [x] Replace stage-progress chrome in `templates/turn.html` with a restrained continuous-chat workspace.
- [x] Wire bottom composer interactions: Enter submit, Shift+Enter newline, mic toggle, and SSE "Thinking…" replacement.
- [x] Preserve partial voice transcript when switching back to text mode in `voice.js`.
- [x] Fix lint/type issues surfaced by the Phase 2.4 changes.
- [x] Run focused UI/voice regression tests, lint, type checks, and full pytest.
- [x] Update `tasks/phase2_tasks.md` for completed Phase 2.4 scope.
- [x] Commit Phase 2.4 and synchronize remote if verification is clean.

## UI Spec
- **Visual thesis:** Calm interview cockpit — warm paper surface, restrained dark/blue actions, and the chat thread as the focused workspace.
- **Content structure:** Header with soft `Problem N of M` progress, full-history newest-bottom thread with subtle problem dividers, sticky composer with adjacent text/voice controls.
- **Interaction thesis:** Message/probe area remains live with SSE token paint; pending examiner output announces "Thinking…" until first token; keyboard submit is fast while preserving multiline answers; voice→text mode switches keep partial transcript state.

## Review
- Added event-log-backed chat projection with `ProblemIntroduced` dividers and candidate-facing problem progress.
- Reworked the turn page into a continuous chat workspace with a sticky bottom composer, text/voice controls, accessible labels, and SSE `Thinking…` first-token affordance.
- Preserved partial voice transcripts when switching back to text input.
- Marked Phase 2.4.1-2.4.10 complete; left Phase 2.4.11 unchecked because a real browser end-to-end three-problem scenario is still outstanding.
- Verification run:
  - `.venv/bin/python -m pytest -q tests/integration/test_continuous_chat_ui.py tests/integration/test_mode_switch_bidirectional.py tests/integration/test_voice_ui_ptt.py tests/integration/test_probe_sse.py tests/integration/test_e2e_voice_text_fallback.py` -> pass.
  - `.venv/bin/python -m ruff check .` -> pass.
  - `.venv/bin/python -m mypy adapters/http/app.py tests/integration/test_continuous_chat_ui.py` -> pass.
  - `.venv/bin/python -m pytest -q` -> pass.
