# InterviewSystem

A project scaffold for an interview system.
Note: This branch contains Phase 0 backbone implementation (domain, events, eventlog, projections, orchestrator, rubric loader, contracts, and tests).
# InterviewSystem — MVP Design Repo

AI-powered technical interview platform for Data Science / ML Engineer candidates. This repo currently holds **design only** — architecture, roadmap, and agent templates. Code ships in Phase 0.

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

| Phase | Ship | Prove it |
|---|---|---|
| 0 | Backbone skeleton (domain, event log, projections, orchestrator, contracts) | Unit test: hand-crafted events → expected next-action |
| 1 | Thin slice end-to-end over HTTP with dummy adapters | Curl test: start session → get turn + score |
| 1.5 | `CandidateIntake` with resume source; USER.md populated; consent screen | Upload resume → USER.md has claims + skills |
| 2 | LLM Challenger, calibrated from USER.md | Golden-prompt test: generated question references rubric dimension |
| 3 | LLM Examiner + humanly texture (streaming, pacing, backchannels, named persona) | Integration test + manual session; latency assertions pass |
| 4 | Jupyter Runtime adapter (warm kernel) | Code cell executes; output attached as artifact |
| 5 | Rationale + Communication Scorers; rubric aggregator | Fixture tests — known turn → expected signals |
| 6 | Candidate UI + Recruiter dashboard | Playwright smoke + manual walkthrough |
| post-MVP | Proctor, ATS sync, voice, bias audit, messy-data generator, LinkedIn/blog/GitHub profile sources | — |

## Non-goals (MVP)

- Multiple specializations beyond DS/ML Engineer.
- Real-time proctoring AI.
- ATS / HRIS integration.
- Voice UX.
- Scale beyond ~50 concurrent sessions.

## Next up

One of:
- Bite-sized TDD task list for Phase 0.
- Python skeleton for `core/` + contracts.
- Streaming wire protocol (WebSocket message schema) for the candidate UI.
