# InterviewSystem

AI-powered technical interview platform for Data Science / ML Engineer candidates. The Phase 0–ζ vertical slice is live: event-sourced backbone, FSM orchestrator, LLM Challenger + Scorers via OpenRouter, sandboxed Python runtime, and a chat-style HTTP UI.

## Quick start

```bash
# 1. Install
uv sync

# 2. Configure LLM (OpenRouter or OpenAI). See .env.example.
cp .env.example .env
$EDITOR .env   # set OPENROUTER_API_KEY=sk-or-v1-...

# 3. Run
uv run uvicorn adapters.http.app:create_app --factory --reload --port 8000

# 4. Open http://localhost:8000 and start an interview.
```

The candidate flow:
1. Land on `/`, enter a handle, click **Start interview**.
2. The Challenger LLM proposes a real-world DS/ML scenario (or a canned prompt if the model misses its 8s deadline).
3. Chat back and forth — interviewer bubbles on the left, your replies on the right. `Enter` sends, `Shift+Enter` newlines, `+ add code` reveals an optional Python cell that runs in a sandboxed subprocess.
4. When the answer budget is hit, two LLM scorers (rationale + communication) run, the rubric aggregator writes `outputs/{session_id}_feedback.md`, and you're redirected to the result page.

Tests: `uv run pytest` (127 unit tests, no network).

## Architecture in one paragraph

A FastAPI surface ([adapters/http/app.py](adapters/http/app.py)) sits over a stateless `SessionRunner` that replays an append-only SQLite event log on every request and asks a pure FSM ([core/orchestrator.py](core/orchestrator.py)) what to do next: ask the Challenger for a question, run candidate code, request the Examiner for a probe, ask a Scorer for a signal, or aggregate the rubric. All LLM I/O goes through a 3-tier `ModelRouter` with a hard wall-clock deadline, automatic fallback chain (top → mid → cheap → canned), and JSON extraction tolerant of markdown fences.

## How to read this folder

Start here, in order:

1. **[architecture-and-plan.md](./architecture-and-plan.md)** — the whole design. 13 sections covering first principles, domain model, backbone, MVP cut, phased roadmap, ADRs, agent-persona-as-data pattern, speed & humanly-interaction constraints, and candidate profile ingestion.
2. **[CHANGELOG.md](./CHANGELOG.md)** — what was decided when.
3. **[CLAUDE.md](./CLAUDE.md)** — project-level operating rules for contributors (AI or human).
4. **`templates/`** — the runtime agent-persona layer. Markdown files loaded by adapters to compose LLM prompts. See map below.

## How to read this folder

Start here, in order:

1. **[architecture-and-plan.md](./architecture-and-plan.md)** — the whole design. 13 sections covering first principles, domain model, backbone, MVP cut, phased roadmap, ADRs, agent-persona-as-data pattern, speed & humanly-interaction constraints, and candidate profile ingestion.
2. **[CHANGELOG.md](./CHANGELOG.md)** — what was decided when.
3. **[CLAUDE.md](./CLAUDE.md)** — project-level operating rules for contributors (AI or human).
4. **`templates/`** — the runtime agent-persona layer. Markdown files loaded by adapters to compose LLM prompts. See map below.

## Template map

```
templates/
├── agents/
│   ├── AGENTS.md                     # shared red lines for every agent
│   ├── challenger/                   # authors the case study
│   │   ├── IDENTITY.md
│   │   ├── SOUL.md
│   │   └── TOOLS.md
│   ├── examiner/                     # skeptical stakeholder, conversational
│   │   ├── IDENTITY.md
│   │   ├── SOUL.md
│   │   ├── PACING.md                 # human-texture rules (latency, backchannels, repair)
│   │   └── TOOLS.md
│   ├── intake/                       # profile ingestion (resume + LinkedIn + blog + GitHub)
│   │   ├── IDENTITY.md
│   │   └── TOOLS.md
│   └── scorers/
│       ├── rationale/                # scores model_rationale dimension
│       │   ├── IDENTITY.md
│       │   ├── SOUL.md
│       │   └── TOOLS.md
│       └── communication/            # scores communication dimension
│           ├── IDENTITY.md
│           ├── SOUL.md
│           └── TOOLS.md
├── rubrics/
│   └── ds-ml-engineer-v1.yaml        # MVP rubric, 4 dimensions, weights hidden from agents
└── session/
    ├── BOOT.md.tmpl                  # per-session snapshot (rubric, role, runtime config)
    ├── BOOTSTRAP.md                  # one-time: author a new interview template
    ├── CONSENT.md                    # candidate-facing consent surface
    ├── HEARTBEAT.md                  # orchestrator tick rules (pacing, async scorers)
    ├── MEMORY.md.tmpl                # curated session notes
    └── USER.md.tmpl                  # candidate profile + rolling observations
```

## Core ideas in one page

- **Domain primitive** — `Session → Turn → Artifact → Signal → Score`. Six objects, nothing more.
- **Event log is truth.** State is a projection. Replayable.
- **Hexagonal core.** Five contracts (`Challenger`, `Examiner`, `Scorer`, `Runtime`, `UIAdapter`) + two support contracts (`ModelRouter`, `ProfileSource`, `PersonaLoader`). Everything else is an adapter.
- **Agent persona as data.** Personas are markdown (`IDENTITY.md` + `SOUL.md` + `TOOLS.md`), not hardcoded prompts.
- **Speed is a constraint, not a polish.** Latency budgets are test assertions from day one. Token streaming + prompt caching + 2-tier model routing + scorers-off-critical-path.
- **Humanly is designed, not emergent.** Named examiner, typing indicator, backchannels, pacing floor, graceful repair, stress dampening, warm open/close, break offer.
- **Profile ingestion is privacy-first.** Per-source consent, PII strip before any agent reads anything, demographic signals dropped, 30-day retention default.

## Roadmap at a glance

| Phase | Ship | Status |
|---|---|---|
| 0 | Backbone skeleton (domain, event log, projections, orchestrator, contracts) | ✅ shipped |
| 1 | Thin slice end-to-end over HTTP with dummy adapters | ✅ shipped |
| 1.5 | `CandidateIntake` with resume source; USER.md populated; consent screen | planned |
| 2 | LLM Challenger, calibrated from USER.md | ✅ shipped (no USER.md yet) |
| 3 | LLM Examiner + humanly texture (streaming, pacing, backchannels, named persona) | partial |
| 4 | Subprocess Runtime adapter (resource-limited) | ✅ shipped |
| 5 | Rationale + Communication Scorers; rubric aggregator | ✅ shipped |
| 6 | Candidate chat UI + Recruiter dashboard | ✅ chat UI; dashboard pending |
| next | Voice UX over the same chat data shape | up next |
| post-MVP | Proctor, ATS sync, bias audit, messy-data generator, LinkedIn/blog/GitHub profile sources | — |

## Non-goals (MVP)

- Multiple specializations beyond DS/ML Engineer.
- Real-time proctoring AI.
- ATS / HRIS integration.
- Scale beyond ~50 concurrent sessions.

## Next up

- Voice surface (STT in, TTS out) reusing the existing `{role, text}` chat data shape.
- `CandidateIntake` so the Challenger can calibrate from a real resume.
- Recruiter dashboard over the event log.

**Runtime Sandbox**

- **Adapter:** [adapters/runtime/subprocess_runtime.py](adapters/runtime/subprocess_runtime.py) — executes candidate Python in a short-lived subprocess and applies POSIX resource limits when available.
- **Bootstrap adapter:** [adapters/runtime/runtime_adapter.py](adapters/runtime/runtime_adapter.py) — wraps the runtime and emits `RuntimeExecuted` / `RuntimeFailed` and `ArtifactAttached` events to the event log.
- **Defaults:** memory cap ~512MB, CPU time cap ~10s (configurable in adapter constructor).
- **Network:** the runtime sets `HTTP_PROXY` / `HTTPS_PROXY` to an unreachable host by default to make outbound network calls fail-fast. This is NOT a security sandbox — it reduces accidental network access but does not prevent all exfiltration.
- **Platform notes:** POSIX `resource` limits and `preexec_fn` are applied only when available (Unix-like systems). On macOS and Linux these help mitigate runaway code; Windows behavior will be more permissive.
- **Safety note:** This sandbox is best-effort. For production isolation use OS-level sandboxing (containers, seccomp, process namespaces) or a dedicated execution service. Treat `SubprocessRuntime` as a developer-grade mitigation, not a security boundary.

