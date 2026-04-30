# Phase 4/7 Hardening, Resume Intake, and Production Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the requested gaps: (1) Phase 4 Examiner completeness, (3) Phase 7 UI/recruiter polish, (4) stronger resume intake, and (5) production hardening/observability/security gates for the chat workflow only.

**Architecture:** Keep the current event-sourced FastAPI/Jinja chat workflow. Add narrowly-scoped production primitives: upload-backed resume intake, candidate-facing pacing/break affordances, recruiter evidence improvements, and in-process metrics/audit/security checks that can later be swapped for OpenTelemetry/Prometheus without changing domain events.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, Pydantic v2, SQLite event log, pytest/ruff/mypy. No voice work. No React rewrite unless explicitly requested; polish the existing server-rendered UI to keep scope deliverable.

---

## Scope mapping

### Item 1 — Phase 4 Examiner completeness
- Implement `PacingFloorReached` event or equivalent durable timing event.
- Add `Pacer` helper for 800ms minimum visible examiner response, idle/break decisions, and no-double-backchannel guard.
- Ensure Examiner prompts include profile claims and recent candidate turns wrapped as untrusted data where candidate-authored text enters prompts.
- Add tests for pacing floor, backchannel no-repeat, break offer threshold, and prompt envelope.

### Item 3 — Phase 7 UI/recruiter polish
- Improve existing Jinja chat UI: typing/thinking state, break offer banner, highlighted source refs, recruiter evidence links back to transcript anchors.
- Add recruiter session list route and drill links.
- Add Playwright-like server-rendered smoke tests via TestClient/HTML assertions, since no browser test stack exists yet.

### Item 4 — Stronger candidate intake
- Add direct resume upload field to start form.
- Implement `ResumeSource` for `.txt`, `.pdf`, `.docx` best-effort parsing.
  - `.txt`: built in.
  - `.pdf`: use optional `pdfminer.six` if installed; otherwise return a clear unsupported-file error.
  - `.docx`: use optional `python-docx` if installed; otherwise return a clear unsupported-file error.
- Persist raw upload only as audit artifact outside prompts; only scrubbed `USER.md` reaches agents.
- Add file-size limit and content-type/extension validation.

### Item 5 — Production hardening / observability / security gates
- Add lightweight in-process metrics collector and `/internal/metrics` endpoint for chat-path latency and failure counters.
- Add hashed LLM audit log records without raw prompt/response text.
- Add prompt-injection envelope helper and adversarial tests for chat/scorer prompt boundaries.
- Add retention/config knobs documented in `.env.example` without implementing a background deletion worker yet.

---

## Task 1: Define events and helpers for pacing/metrics/audit

**Files:**
- Modify: `core/events.py`
- Create: `core/pacing.py`
- Create: `core/observability.py`
- Tests: `tests/unit/test_pacing.py`, `tests/unit/test_observability.py`

Steps:
1. Write failing tests for `Pacer.should_backchannel`, `Pacer.should_offer_break`, `Pacer.apply_floor`.
2. Add `PacingFloorReached` event with `turn_id`, `floor_ms`, `slept_ms`.
3. Add `MetricSink` with counters/histograms stored in memory and text export.
4. Add `AuditLogger` that stores hashes/metadata only.
5. Verify unit tests pass.

## Task 2: Wire Phase 4 pacing into chat examiner path

**Files:**
- Modify: `adapters/http/session_runner.py`
- Modify: `adapters/http/app.py`
- Modify: `adapters/http/templates/turn.html`
- Tests: `tests/integration/test_examiner_pacing_floor.py`, `tests/integration/test_break_offer_ui.py`

Steps:
1. Write failing tests for 800ms minimum examiner response, one backchannel max before a probe, and break banner after threshold.
2. Inject `Pacer` into `SessionRunner`.
3. Emit `PacingFloorReached` and `BreakDue` events.
4. Render break offer in chat UI.
5. Verify focused tests and existing chat tests pass.

## Task 3: Prompt-injection envelope for candidate-authored prompt context

**Files:**
- Create: `core/prompt_safety.py`
- Modify: `adapters/examiner/llm_examiner.py`
- Modify: `adapters/scorer/_base.py`
- Tests: `tests/unit/test_prompt_safety.py`, `tests/adversarial/injection/test_chat_prompt_injection.py`

Steps:
1. Write failing tests for tag smuggling and rubric-leak/score-inflation fixtures.
2. Implement `sanitize_envelope_text()` and `candidate_turn_envelope()`.
3. Use envelopes for problem transcripts and scorer artifact bodies when candidate-authored.
4. Add red-line prompt text to Examiner/Scorer composed prompts.
5. Verify adversarial tests pass.

## Task 4: Resume upload source

**Files:**
- Create: `core/profile_sources.py`
- Modify: `core/candidate_intake.py`
- Modify: `adapters/http/app.py`
- Modify: `adapters/http/templates/start.html`
- Tests: `tests/unit/test_resume_source.py`, `tests/integration/test_resume_upload_intake.py`

Steps:
1. Write failing tests for `.txt` resume upload, max file size, unsupported extension, and PII stripping.
2. Add `ResumeSource` and `ProfileFragment`.
3. Wire optional `UploadFile` into `/sessions`.
4. Store raw upload in `outputs/sessions/<id>/artifacts/profile/` and only render scrubbed summary to `USER.md`.
5. Verify intake tests pass.

## Task 5: Recruiter evidence polish

**Files:**
- Modify: `adapters/http/app.py`
- Modify: `adapters/http/templates/recruiter_session.html`
- Create: `adapters/http/templates/recruiter_sessions.html`
- Tests: `tests/integration/test_recruiter_session_list.py`, `tests/integration/test_recruiter_evidence_transcript_links.py`

Steps:
1. Write failing tests for `/recruiter/sessions`, transcript anchors, source-ref links, partial-score badges.
2. Add session-list projection from event log.
3. Add transcript rendering with `id="turn-<id>"` anchors.
4. Link signal source refs to anchors where possible.
5. Verify recruiter evidence tests pass.

## Task 6: Internal metrics and audit endpoints

**Files:**
- Modify: `adapters/http/app.py`
- Modify: `adapters/llm/router.py`
- Create: `adapters/http/templates/internal_metrics.html` only if HTML needed; otherwise plain text.
- Tests: `tests/integration/test_internal_metrics.py`, `tests/unit/test_llm_audit_hashes.py`

Steps:
1. Write failing tests for `/internal/metrics` containing chat latency counters and no raw prompt text.
2. Record route latency and LLM call metadata hashes.
3. Expose text metrics endpoint.
4. Verify tests pass.

## Task 7: Documentation and final gates

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `architecture-and-plan.md` status notes if needed
- Modify: `tasks/todo.md`

Steps:
1. Document chat-only production hardening status and voice deferral.
2. Document resume upload supported formats and optional parser dependencies.
3. Run:
   - `rtk uv run ruff check core adapters tests`
   - `rtk uv run mypy core adapters tests/unit`
   - `rtk uv run pytest -q`
   - `rtk git diff --check`
4. Commit and push only after all pass.

---

## Risks / explicit trade-offs

- This plan does **not** implement a full React shell; it polishes the current server-rendered chat UI to avoid a broad frontend rewrite.
- PDF/DOCX parsing is best-effort and optional unless dependencies are added to `pyproject.toml`; `.txt` resume upload is the reliable baseline.
- Observability is an MVP in-process endpoint, not a full OpenTelemetry/Prometheus deployment.
- Prompt-injection tests will cover deterministic local prompt construction; they will not prove model behavior under all real provider responses.
