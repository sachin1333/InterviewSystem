# Current Task — Single OpenAI GPT-5.5 Fast Model

## Design
- Keep the existing `ModelRouter` call interface so challenger/examiner/scorer code does not need broad refactors.
- Remove production provider/model selection based on OpenRouter or top/mid/cheap OpenAI model env vars.
- Make production LLM creation OpenAI-only: load `.env`, require `OPENAI_API_KEY`, use one model (`gpt-5.5`) with fast reasoning effort (`low`).
- Preserve `FAKE_LLM=1` for offline tests and local deterministic demos.
- Update docs/config so setup points to OpenAI instead of OpenRouter/tiered routing.

## Implementation plan
- [x] Add failing unit tests for OpenAI single-model payloads and factory behavior.
- [x] Run focused tests to verify RED state.
- [x] Rewrite `OpenAIRouter` to ignore tiers for model choice and send `gpt-5.5` + fast reasoning.
- [x] Change `get_model_router()` to load `.env`, ignore OpenRouter, and require `OPENAI_API_KEY` unless `FAKE_LLM=1`.
- [x] Update create-app tests to opt into `FAKE_LLM=1` instead of depending on local secrets.
- [x] Update README, `.env.example`, and model config docs for single OpenAI model usage.
- [x] Run focused tests, ruff, mypy, and full pytest.

## Review
### RED verification
- `rtk uv run pytest -q tests/unit/test_openai_router.py tests/unit/test_llm_factory.py` failed as expected: factory still preferred OpenRouter, did not support dotenv path loading, and OpenAIRouter still lacked the urllib-based single-model payload seam the new tests exercise.

### GREEN focused verification
- `rtk uv run pytest -q tests/unit/test_openai_router.py tests/unit/test_llm_factory.py tests/integration/test_problem_bank_session.py::test_create_app_loads_problem_bank_for_new_sessions tests/integration/test_problem_bank_session.py::test_create_app_chat_rubric_matches_scored_dimensions` -> 8 passed.


### Docs/config update
- README and `.env.example` now describe OpenAI-only production config with `OPENAI_MODEL=gpt-5.5` and `OPENAI_REASONING_EFFORT=low`.
- `config/models.yaml`, `config/tier_overrides.yaml`, `adapters/llm/task_tier_map.py`, and `adapters/llm/router.py` now document that tier labels no longer select production models.

### Final verification
- Focused: `rtk uv run pytest -q tests/unit/test_openai_router.py tests/unit/test_llm_factory.py` -> 6 passed.
- Focused + router/create_app: `rtk uv run pytest -q tests/unit/test_openai_router.py tests/unit/test_llm_factory.py tests/unit/test_model_router.py tests/unit/test_router_streaming.py tests/unit/test_router_iter_streaming.py tests/integration/test_problem_bank_session.py::test_create_app_loads_problem_bank_for_new_sessions tests/integration/test_problem_bank_session.py::test_create_app_chat_rubric_matches_scored_dimensions` -> 18 passed.
- Ruff: `rtk uv run ruff check adapters/llm/openai_router.py adapters/llm/factory.py adapters/llm/router.py adapters/llm/task_tier_map.py tests/unit/test_openai_router.py tests/unit/test_llm_factory.py tests/integration/test_problem_bank_session.py` -> pass.
- Mypy: `rtk uv run mypy adapters/llm/openai_router.py adapters/llm/factory.py adapters/llm/router.py adapters/llm/task_tier_map.py tests/unit/test_openai_router.py tests/unit/test_llm_factory.py tests/integration/test_problem_bank_session.py` -> pass.
- Full suite: `rtk uv run pytest -q` -> pass.
- Diff check: `rtk git diff --check` -> pass.

---

# Current Task — Candidate Details Personalization MVP

## Design
- Add an MVP candidate intake path on the start form using optional structured text fields: target role, years of experience, declared skills, pasted resume/profile notes.
- Convert submitted details into a privacy-scrubbed `ProfileIngested` event and materialized `USER.md`; do not pass raw resume text directly to agents.
- Inject the rendered `USER.md` into Challenger and Examiner prompt assembly so generated openers/probes can personalize questions from declared skills and claims.
- Keep existing flows working when no candidate details are supplied.

## Implementation plan
- [x] Add failing tests for profile event creation, USER.md materialization/redaction, and prompt-context injection.
- [x] Run focused tests to verify RED state.
- [ ] Implement profile event fields and candidate intake rendering/redaction helpers.
- [ ] Wire optional start-form fields into session creation and persist `USER.md`.
- [ ] Inject `USER.md` into Challenger/Examiner prompts without exposing raw inputs.
- [ ] Update start UI and docs.
- [ ] Run focused tests, ruff, mypy, and relevant integration tests.

## Review
### RED verification
- `rtk uv run pytest -q tests/unit/test_candidate_intake.py tests/unit/test_personalized_prompts.py tests/integration/test_candidate_intake_http.py` failed during collection with `ModuleNotFoundError: No module named 'core.candidate_intake'`, proving the new intake API is not implemented yet.


---

# Current Task — Plan Remaining MVP Implementation Scope

## Scope
- Include: Candidate Profile Ingestion, Challenger personalization, Examiner completion, Recruiter Evidence View, Human Override workflow, Scorer evidence/partial-score hardening.
- Skip for now: sandboxed Jupyter/gVisor runtime, observability dashboards/tracing/metrics, full security/privacy hardening, post-MVP/v1 items.

## Plan
- [x] Inspect architecture roadmap and current implementation status.
- [x] Create implementation plan document with tasks, files, tests, and verification gates.
- [x] User requested implementation start; implemented scoped MVP slices.

## Review
- Plan saved to `docs/superpowers/plans/2026-04-30-remaining-mvp-without-platform-hardening.md`.
- Note: previous interrupted implementation left uncommitted test files in the working tree; implementation execution should either keep/adapt those tests or clean them deliberately before starting Task 1.


## Implementation Review — 2026-04-30 remaining MVP scope

### Shipped
- Candidate detail intake via optional start-form fields.
- Privacy-scrubbed `ProfileIngested` event and `outputs/sessions/<session_id>/USER.md` materialization.
- `USER.md` dynamic prompt context for Challenger and Examiner.
- Examiner backchannel event before probes, rendered in chat.
- Signal `id` and `justification` fields with scorer fallback justifications.
- Reviewer override signals preferred by aggregation.
- Server-rendered recruiter evidence page and override POST route.

### Verification
- `rtk uv run pytest -q tests/unit/test_candidate_intake.py tests/unit/test_events.py` -> 15 passed.
- `rtk uv run pytest -q tests/integration/test_candidate_intake_http.py tests/integration/test_http_happy.py` -> 4 passed.
- `rtk uv run pytest -q tests/unit/test_personalized_prompts.py tests/integration/test_candidate_intake_http.py` -> 3 passed.
- `rtk uv run pytest -q tests/integration/test_examiner_personalized_pacing.py tests/integration/test_text_socratic_flow.py tests/integration/test_probe_persistence.py` -> 4 passed.
- `rtk uv run pytest -q tests/unit/test_scorer_evidence.py tests/unit/test_aggregator.py tests/unit/test_domain.py` -> 13 passed.
- `rtk uv run pytest -q tests/unit/test_human_override.py tests/unit/test_events.py tests/unit/test_aggregator.py` -> 17 passed.
- `rtk uv run pytest -q tests/integration/test_recruiter_evidence_view.py` -> 2 passed.
- Focused combined suite -> 17 passed.
- `rtk uv run ruff check ...` -> pass after import sorting.
- `rtk uv run mypy ...` -> success for touched typed modules/unit tests.
- `rtk uv run pytest -q` -> full suite passed.
- `rtk git diff --check` -> pass.

### Still deferred by request
- Strong sandbox runtime (gVisor/Firecracker/Jupyter).
- Observability platform and dashboards.
- Full security/privacy hardening beyond MVP redaction.
- Post-MVP/v1 integrations and differentiators.

---

# Current Task — Chat Workflow Robustness and Realtime Verification

## Scope
- Candidate chat workflow only: start session, personalized context intake, challenger opener, answer submission, examiner probe/backchannel, scoring/aggregation, result and recruiter evidence read paths.
- Voice workflow deferred.
- Realtime target: candidate-facing chat requests should complete in 1–3 seconds under normal model latency assumptions; local deterministic fake path should stay far below that.

## Plan
- [x] Run focused chat workflow robustness tests.
- [x] Run latency-focused tests and collect wall-clock timings for start, turn submit, and next interviewer response.
- [x] Run full regression suite to catch cross-cutting breakage.
- [x] If failures occur, root-cause before fixing.
- [x] Document evidence and any caveats.

## Review
- Focused chat robustness suite: `rtk uv run pytest -q tests/integration/test_http_happy.py tests/integration/test_continuous_chat_ui.py tests/integration/test_text_socratic_flow.py tests/integration/test_chat_turn_submission_guards.py tests/integration/test_chat_advance_idempotency.py tests/integration/test_http_double_submit.py tests/integration/test_e2e_happy.py tests/integration/test_e2e_double_submit.py tests/integration/test_e2e_concurrent.py tests/integration/test_e2e_crash_resume.py tests/integration/test_e2e_llm_chaos.py tests/integration/test_e2e_runtime_chaos.py tests/integration/test_first_paint_latency.py tests/integration/test_turn_timing_events.py tests/integration/test_probe_sse.py tests/integration/test_probe_persistence.py tests/integration/test_candidate_intake_http.py tests/integration/test_examiner_personalized_pacing.py tests/integration/test_recruiter_evidence_view.py tests/integration/test_chat_latency_budget.py` -> 42 passed.
- Latency smoke with simulated 750ms/model call after fix:
  - `POST /sessions -> first chat paint`: 0.772s.
  - `POST answer (redirect issued)`: 0.003s.
  - `GET /sessions -> examiner probe paint`: 1.508s.
- Root cause found and fixed: `LlmExaminer.review()` retried around `ModelRouter.call_json()`, while `call_json()` already performs bounded JSON retry. Malformed examiner JSON therefore caused four provider calls and crossed 3s. Removed duplicate adapter-level retry.
- Quality gates: `rtk uv run ruff check core adapters tests` -> pass; `rtk uv run mypy core adapters tests/unit/test_candidate_intake.py tests/unit/test_personalized_prompts.py tests/unit/test_scorer_evidence.py tests/unit/test_human_override.py` -> pass; `rtk uv run pytest -q` -> full suite pass; `rtk git diff --check` -> pass.
- Caveat: latency test uses deterministic `FakeRouter` with simulated model delay. Real OpenAI latency can still vary by network/provider load; this verifies app-side chat orchestration stays inside the 1–3s target when the model dependency is moderately slow.

---

# Current Task — Implement Requested Items 1, 3, 4, 5

## Requested scope
- [x] 1. Phase 4 Examiner completeness.
- [x] 3. Phase 7 UI/recruiter polish.
- [x] 4. Stronger candidate intake with resume upload/parser.
- [x] 5. Production hardening / observability / security gates.

## Plan
- [x] Inspect current roadmap/status and map requested item numbers to concrete gaps.
- [x] Write implementation plan: `docs/superpowers/plans/2026-04-30-phase4-7-hardening-intake-observability.md`.
- [x] Scope approved; implemented and verified.

## Review
- Added pacing helpers/events, examiner pacing floor, break offer UI, and no-repeat backchannel guard.
- Added prompt-safety envelope helpers and adversarial scorer prompt test.
- Added resume upload source with `.txt` support and optional PDF/DOCX parsing hooks; raw upload is stored under session profile artifacts and scrubbed `USER.md` feeds agents.
- Added recruiter session list and evidence source anchor links.
- Added lightweight `/internal/metrics` and hash-only LLM audit helper.
- Rewrote README to reflect current chat-first capabilities and deferred voice/full-sandbox scope.

## 2026-05-04 — Implement latency/profile/scoring plan

Plan: `docs/superpowers/plans/2026-05-04-latency-profile-alignment-scoring.md`
Branch/worktree: `feature/latency-profile-scoring` at `.worktrees/latency-profile-scoring`

- [x] Create isolated worktree and verify targeted baseline tests.
- [x] Wave 1A: profile-aware selector module/tests.
- [x] Wave 1B: OpenAI structured outputs support/tests.
- [x] Wave 1C: scoring request/completion events and projection tests.
- [x] Integrate Wave 1 in SessionRunner.
- [x] Wave 2A: problem-level combined scorer.
- [x] Wave 2B: background scoring worker.
- [x] Wave 3A: async scoring integration and latency test.
- [x] Wave 3B: result pending/partial/final score UX.
- [x] Wave 3C: metrics and latency probe updates.
- [x] Final verification and review notes.


### Review — latency/profile/scoring implementation
- Implemented profile-aware deterministic problem selection with persisted selection rationale.
- Added OpenAI provider-native structured output request support and strict scoring schema helpers.
- Added scoring request/completion events and pending/completed score projection state.
- Added problem-level scorer with one structured LLM call per problem and deterministic bounded fallback.
- Added in-process background scoring worker and production wiring in `create_app()`.
- Updated `SessionRunner` to emit `ScoringRequested` and enqueue jobs when problems close, avoiding synchronous scorer calls on candidate-facing close/advance path when worker is configured.
- Updated result page to show pending, partial, and final scoring states.
- Added metrics helpers and latency probe stage reporting.
- Verification run so far:
  - targeted combined suite: 72 passed.
  - full suite: `rtk uv run pytest -q` passed.
  - `rtk uv run ruff check core adapters tools tests` passed.
  - `rtk uv run mypy core adapters tests/unit/test_problem_selection.py tests/unit/test_problem_scorer.py tests/unit/test_background_scoring.py` passed.
  - `rtk git diff --check` passed.
- Final review subagent is still running; address any blocking findings before marking final item complete.


### Follow-up review fixes
- Fixed async final aggregation for problem-scoped background scoring by finalizing when all durable `ScoringRequested` problem jobs are completed, independent of legacy artifact × global-dimension scorer loop.
- Added result/advance recovery for durable pending scoring requests so a restarted in-process worker can re-enqueue unfinished jobs from the event log.
- Fixed strict structured-output schemas for OpenAI strict mode and wired examiner calls to provider-native schema.
- Integrated LLM/scoring metrics and normalized HTTP metric route labels to avoid session IDs in metrics.
- Made legacy scorer parser reject string scores.
- Added regression tests for async final score and pending-job recovery.
