# CHANGELOG

All design decisions dated. Latest first.

## 2026-04-21

### Added
- **§12 Speed & Humanly Interaction** (architecture-and-plan.md)
  - Latency budgets table: first-token <800 ms, backchannel <300 ms, scorers off critical path.
  - Techniques ranked: token streaming, prompt cache on static persona layers, 2-tier model routing (`fast` / `strong`), speculative pre-generation, async scorers, warm-start, region colocation, context trim.
  - Humanly design contract: named persona, streaming, typing indicator, backchannels, 800 ms pacing floor, never-interrupt, graceful repair, stress dampening, warm open/close, break offer, no AI disclaimers mid-flow.
  - New `Turn.kind` values: `greeting`, `backchannel`, `nudge`, `closer`.
  - New events: `CandidateTyping`, `CandidateIdle`, `BackchannelPosted`.
  - New contract: `ModelRouter(tier, prompt, stream)`.
- **§13 Candidate Profile Ingestion** (architecture-and-plan.md)
  - New contract: `ProfileSource(name, handle) -> ProfileFragment`. MVP sources: resume, linkedin (manual paste), blog, github.
  - New adapter: `CandidateIntake` — runs once per `(candidate, role)`, emits `ProfileIngested`, materializes `USER.md`.
  - Privacy rules: explicit per-source consent, PII strip (name, email, phone, address, photo, colleague names), no demographic inference, 30-day retention, writing-style only as soft prior never a hard flag.
  - New Phase 1.5 slot in roadmap.
- **Templates**
  - `agents/examiner/PACING.md` — human-texture rules.
  - `agents/intake/IDENTITY.md` + `TOOLS.md` — profile extractor.
  - `session/CONSENT.md` — candidate-facing consent surface.
  - `session/USER.md.tmpl` — extended with Declared Skills / Projects / Publications / Claims sections.
  - `session/HEARTBEAT.md` — rewritten: typing-aware, 800 ms floor, async scorer dispatch, greeting / break / closer rules.
- **Repo navigation**
  - `README.md` — folder-wide index and orientation.
  - `CHANGELOG.md` — this file.

### Decisions locked
- Scorers run async. Candidate never waits on scoring.
- Never post a system turn while the candidate is typing.
- Static persona layers (AGENTS/IDENTITY/SOUL/TOOLS/BOOT) are prompt-cached; only turn deltas re-sent.
- Examiner never self-identifies as AI mid-session. Full disclosure lives in `CONSENT.md`.
- PII stripped before any agent receives any profile data.

---

## 2026-04-21 (earlier)

### Added
- **§0–10 core architecture** (architecture-and-plan.md)
  - First principles.
  - Domain model: `Session`, `Turn`, `Artifact`, `Signal`, `Rubric`, `Score`.
  - Hexagonal backbone: domain + event log + projections + orchestrator + five contracts (`Challenger`, `Examiner`, `Scorer`, `Runtime`, `UIAdapter`).
  - MVP cut: one role, one rubric, two scorers, Jupyter runtime, Postgres-only.
  - Six-phase backbone-first roadmap.
  - Six ADRs: event log as truth, hexagonal, one `Turn` primitive, pure orchestrator, Postgres-only for MVP, LLM behind adapters.
  - Suggested directory shape.
- **§11 Agent Persona as Data** (architecture-and-plan.md)
  - Openclaw-inspired template pattern.
  - Session workspace layout on disk.
  - Shared vs per-session template resolution.
  - New contract: `PersonaLoader`.
  - `BOOTSTRAP` (once per template) vs `BOOT` (once per session) split.
- **Templates (first batch)**
  - `agents/AGENTS.md` — shared red lines.
  - `agents/challenger/{IDENTITY,SOUL,TOOLS}.md`.
  - `agents/examiner/{IDENTITY,SOUL,TOOLS}.md`.
  - `agents/scorers/{rationale,communication}/{IDENTITY,SOUL,TOOLS}.md`.
  - `session/BOOT.md.tmpl`, `USER.md.tmpl`, `MEMORY.md.tmpl`, `BOOTSTRAP.md`, `HEARTBEAT.md`.
  - `rubrics/ds-ml-engineer-v1.yaml` — 4-dimension MVP rubric.
