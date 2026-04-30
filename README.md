# InterviewSystem

AI-powered technical interview platform for Data Science / ML Engineer candidates. The current chat workflow is event-sourced end-to-end: candidate intake, personalized Challenger/Examiner prompts, continuous chat, optional code execution, LLM scoring, recruiter evidence, lightweight metrics, and deterministic offline tests. Voice remains feature-flagged and is not the current production focus.

## Quick start

```bash
uv sync
cp .env.example .env
$EDITOR .env   # set OPENAI_API_KEY=sk-... or FAKE_LLM=1 for offline demos
uv run uvicorn adapters.http.app:create_app --factory --reload --port 8000
```

Open <http://localhost:8000>.

## Candidate chat flow

1. Candidate enters a handle and optional personalization context.
2. Candidate may paste resume/profile text or upload a `.txt`, `.pdf`, or `.docx` resume. `.txt` works without extra dependencies; PDF/DOCX parsing is best-effort via optional parser packages when installed.
3. Intake strips common PII, writes `outputs/sessions/<session_id>/USER.md`, and emits `ProfileIngested`.
4. Challenger asks a personalized DS/ML problem.
5. Candidate chats in a continuous thread. `Enter` sends, `Shift+Enter` adds a newline, and `+ add code` reveals an optional Python cell.
6. Examiner probes with backchannels, pacing floor, graceful fallback, and profile-aware follow-ups.
7. Scorers run off the candidate-facing path; the result page shows composite feedback.

Realtime chat budget is tested with deterministic latency smoke tests. The app-side chat orchestration stays within the 1–3s target under simulated slow model calls; real provider/network latency can still vary.

## Recruiter workflow

- `/recruiter/sessions` lists sessions.
- `/recruiter/sessions/{session_id}` shows composite score, per-dimension score evidence, signal justifications, source refs, transcript anchors, and human override controls.
- Human overrides append audit events and reviewer signals; they do not replace original scorer signals.

## Internal operations

- `/internal/metrics` exposes lightweight Prometheus-style in-process counters/histograms for chat/recruiter request latency.
- LLM audit helpers record prompt/response hashes and metadata only, never raw prompt text.
- Candidate-authored prompt context is wrapped as untrusted data before entering scorer/examiner prompts.

## Voice mode

Voice is still available behind feature flags, but chat is the current tested scope.

| Variable | Default | Purpose |
|---|---:|---|
| `VOICE_MODE` | `off` | `off`, `on`, or `forced` voice UI policy |
| `TTS_MODE` | `off` | Enables audio playback when `on` |
| `VOICE_LATENCY_BUDGET_MS` | `800` | Voice soft latency budget |
| `WISPR_API_KEY` | — | Optional real STT provider |
| `CARTESIA_API_KEY` | — | Optional primary TTS provider |
| `ELEVENLABS_API_KEY` | — | Optional TTS fallback |

## Architecture in one paragraph

A FastAPI surface ([`adapters/http/app.py`](adapters/http/app.py)) sits over a stateless `SessionRunner` that replays an append-only SQLite event log and asks the pure FSM ([`core/orchestrator.py`](core/orchestrator.py)) what to do next: ask the Challenger, request candidate input, execute code, probe with the Examiner, score artifacts, or aggregate. LLM-backed adapters load markdown personas from `templates/agents/`. Production LLM I/O uses one OpenAI model (`OPENAI_MODEL`, default `gpt-5.5`) with fast reasoning (`OPENAI_REASONING_EFFORT=low`); tests use deterministic fakes.

## Important paths

```text
adapters/http/                 FastAPI routes, chat UI, recruiter UI
adapters/challenger/           LLM Challenger adapter
adapters/examiner/             LLM Examiner adapter
adapters/scorer/               LLM scorers and aggregator
adapters/runtime/              Best-effort subprocess runtime
core/                          Domain, events, projections, orchestrator, intake, safety
config/                        Model/tier compatibility config
templates/agents/              Persona markdown for Challenger, Examiner, Scorers
templates/rubrics/             Rubric YAML
templates/session/             BOOT/CONSENT/USER/MEMORY templates
tests/                         Unit, integration, chaos, adversarial tests
```

## Core ideas

- **Event log is truth.** Every stateful change is replayable.
- **Small domain model.** `Session → Turn → Artifact → Signal → Score`.
- **Ports and adapters.** LLMs, runtime, DB, HTTP, and UI sit outside core.
- **Persona as data.** Agent behavior lives in markdown templates.
- **Profile-aware, privacy-first.** Agents read scrubbed `USER.md`, not raw resumes.
- **Candidate text is untrusted.** Prompt envelopes mark candidate-authored content as data, not instructions.
- **Scorers stay off the hot path.** Candidate-facing latency is protected.

## Roadmap status

| Architecture phase | Status |
|---|---|
| Phase 0 — Backbone skeleton | ✅ shipped |
| Phase 1 — End-to-end thin slice | ✅ shipped |
| Phase 2 — Candidate Profile Ingestion | ✅ text + upload MVP shipped; LinkedIn/blog/GitHub deferred |
| Phase 3 — Real Challenger | ✅ shipped with `USER.md` calibration |
| Phase 4 — Real Examiner | ✅ chat MVP hardened: backchannels, pacing floor, break offer, prompt envelope, fallback |
| Phase 5 — Notebook Runtime | ⚠️ developer-grade subprocess runtime; full Jupyter/gVisor/Firecracker sandbox deferred |
| Phase 6 — Scorers + Aggregator + Evidence | ✅ shipped with justifications, partial states, reviewer overrides |
| Phase 7 — Candidate UI + Recruiter Evidence | ✅ server-rendered chat/recruiter MVP; full React shell deferred |
| Production gates | ✅ lightweight metrics/audit/adversarial checks; full OpenTelemetry/Prometheus/security program deferred |

## Runtime sandbox note

The current runtime is [`adapters/runtime/subprocess_runtime.py`](adapters/runtime/subprocess_runtime.py). It applies best-effort POSIX resource limits and proxy-based network fail-fast behavior. It is **not** a production security boundary. For production isolation, use a dedicated sandbox service, containers/seccomp, gVisor, or Firecracker.

## Testing

```bash
uv run ruff check core adapters tests
uv run mypy core adapters tests/unit/test_pacing.py tests/unit/test_observability.py tests/unit/test_prompt_safety.py tests/unit/test_resume_source.py tests/unit/test_llm_audit_hashes.py
uv run pytest -q
```

The full pytest suite runs offline with fake LLM/STT/TTS providers.
