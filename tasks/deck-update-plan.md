# Arch Review Deck — Update Plan

**Date:** 2026-04-24
**Scope:** Fold 3 new design pillars into `InterviewSystem_ArchReview.pptx`.
**Source file:** `/sessions/confident-happy-edison/deckbuild/arch_build.js` (1180 lines)
**Output:** `/sessions/confident-happy-edison/mnt/InterviewSystem/InterviewSystem_ArchReview.pptx`

---

## Confirmed pillars (user 2026-04-24)

| # | Pillar | Interpretation |
|---|--------|----------------|
| 1 | Skeptic latency | Skeptic dialogue turn **P95 ≤ 2s** (was 3s). Scoring async. |
| 2 | Domain-agnostic | **Domain-agnostic engine + domain packs ship separately.** Rubric/challenge/persona/notebook-boilerplate = first-class abstractions. |
| 3 | Cost routing | **3-tier router + task taxonomy.** Top=Kimi K2.6 via OpenRouter; Mid=DeepSeek V3; Cheap=Llama 3.1 8B / Gemini Flash. Per-session budget guardrail. |

---

## Slide change map

Deck currently 16 slides. After update: **17 slides** (insert new Domain Packs after S7).

| Slide | Action | Key edits |
|-------|--------|-----------|
| S1 Title | keep | no change |
| S2 Context/NFR | **edit** | Latency row: `≤ 3s` → `≤ 2s`. Cost row: `≤ $0.80` → `≤ $0.30`. Add note under primary goals: "DS/ML is launch pack; engine is domain-agnostic." |
| S3 Personas | **edit** | Add 5th tile: `Domain Pack Author` (Senior IC per vertical; owns rubric + persona for a pack). Drop open-question about 3rd-party recruiter. New open: "Can Pack Author be external (customer-authored) or internal-only?" |
| S4 C4 L1 | **edit** | Add external actor `OpenRouter` (replaces generic LLM Provider). Keep Senior DS Calibrator as-is (now "Domain Calibrator"). |
| S5 C4 L2 | **edit** | Add 2 containers: `LLM Router` (between orchestration and provider) and `Pack Registry` (DB/artifact store). Update arrows: Skeptic Bot + Scoring Engine both talk to LLM Router, not provider directly. Pack Registry feeds Challenge Gen + Skeptic Bot persona lookup. |
| S6 Data flow | **edit** | Step 4 timing `3s/turn` → `2s/turn`. Add "pack loaded" subtitle to step 1 Dispatch. |
| S7 AI Stack | **rewrite** | New title: "LLM Routing & Cost". 3-tier router diagram (Top/Mid/Cheap) with per-tier model + per-tier $/1M tokens. Task→tier table (6 rows: skeptic draft→Top; rubric grade→Top; intent detect→Cheap; guardrail JSON repair→Cheap; rubric retrieval→local embed; persona format→Mid). Budget guardrail callout: stop-loss at $0.40/session. |
| **S8 NEW Domain Packs** | **insert** | Pack anatomy (4 components: rubric library, challenge templates, skeptic persona, notebook boilerplate). Authoring flow: Pack Author → YAML → lint+eval in staging → version + publish to Pack Registry. 3 launch packs: `ds-ml-v1`, `backend-swe-v1`, `product-analyst-v1`. Multi-tenant: pack selected via ATS job-req metadata. Open: "Pack eval harness — who builds golden sets per pack?" |
| S9 (was S8) Skeptic Bot | keep (renumber) | no content change |
| S10 (was S9) Scoring Engine | keep (renumber) | no content change |
| S11 (was S10) Data | keep (renumber) | add 5th tile mention: Pack Registry lives in S3 (versioned YAML + JSON schemas). |
| S12 (was S11) SLO & cost | **edit** | Skeptic P95 ≤ 2s. Cost envelope: LLM ~$0.22 (was $0.55), compute $0.05 (was $0.12), storage $0.03 (was $0.08). Target ≤ $0.30 (was $0.80). |
| S13 (was S12) Security | keep (renumber) | consider adding pack supply-chain note — defer if tight. |
| S14 (was S13) Failure modes | **edit** | Add row: `Kimi K2.6 upstream outage → auto-tier-shift to DeepSeek V3 for Top tasks; degrade accuracy by ~4 rubric pts`. Keep existing 7 rows. |
| S15 (was S14) Observability | **edit** | LLM ops card: add `per-tier cost/session streamed`, `router-decision log`, `pack version pinned per session`. |
| S16 (was S15) Tradeoffs | **edit** | Replace 4 cards: (a) Monolith vs services — keep; (b) **3-tier router (current) vs flat top-model** — new; (c) **Domain packs (current) vs monolithic rubric** — new; (d) Warm pool vs managed kernels — keep. |
| S17 (was S16) Asks | **edit** | Replace 2 asks. New 6-item list: 1. Router tiering (is 3 right, or is 2 enough?). 2. Domain pack abstraction (too granular / too coarse?). 3. Skeptic 2s feasibility at Top tier (K2.6 TTFT). 4. Pack authoring (external-safe?). 5. Arch shape (6 services). 6. Hidden risk. |

---

## Code-level tasks (arch_build.js)

### 1. Constants + helpers

- Add palette hook: `TIER_TOP = ACCENT`, `TIER_MID = "7DD3FC"` (cyan-300), `TIER_CHEAP = "BAE6FD"` (cyan-200).
- Update `TOTAL = 17`.

### 2. Per-slide edits — minimal patches

Most slides are self-contained IIFE blocks. Pattern: locate block, edit strings only. No layout shifts unless content density changes.

- **S2**: edit lines ~286–300 (NFR array). Change 2 cells. Insert 1 note under Primary goals card.
- **S4**: string swap `"LLM Provider"` → `"OpenRouter"` and sub to `"model routing gateway"`.
- **S5**: add 2 `c4Box()` calls. Shift Skeptic/Scoring arrows to route via `LLM Router` box. Cramped — may need 0.2" squeeze.
- **S6**: swap `"3 s/turn"` → `"2 s/turn"`.
- **S7**: full rewrite. Drop existing 4-card grid. Replace with: 3 tier rows at top (each: tier chip + model + $/1M in + $/1M out), then task→tier table below.
- **S8 NEW**: fresh IIFE. Header `"Extensibility · Domain Packs"`. Layout: left 4 pack-anatomy cards, right authoring flow 4-step horizontal. Bottom: 3 pack chips (`ds-ml-v1`, `backend-swe-v1`, `product-analyst-v1`).
- **S11** (data): add retention row for Pack Registry.
- **S12** (SLO): edit SLO table + cost card numbers.
- **S14** (failure): add row.
- **S15** (observability): edit LLM ops pts array.
- **S16** (tradeoffs): edit 2 of 4 cards.
- **S17** (asks): edit 2 of 6 ask entries.

### 3. Renumber

Each slide has `footer(s, N, TOTAL)` call. Slides 8–16 shift to 9–17. Sed pattern:
```
footer(s, 8,  TOTAL) → footer(s, 9,  TOTAL)
footer(s, 9,  TOTAL) → footer(s, 10, TOTAL)
... etc
```
Do in reverse order to avoid double-increment.

### 4. Build + QA pipeline

```
node arch_build.js
python3 .../soffice.py --headless --convert-to pdf InterviewSystem_ArchReview.pptx
rm slide-*.jpg
pdftoppm -jpeg -r 110 InterviewSystem_ArchReview.pdf slide
# Read tool on slide-01..17.jpg
```

Known risk areas:
- **S5** more containers → arrow tangle. Budget extra pass for arrow routing.
- **S7** full rewrite → new layout may overflow. Test with short + long task names.
- **S8 new** → unknown density until rendered.

### 5. Delivery

Copy final to `/sessions/confident-happy-edison/mnt/InterviewSystem/InterviewSystem_ArchReview.pptx`. Reply with `computer://` link.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| S5 arrow spaghetti with 13 containers | High | Pre-sketch on paper-equivalent: list all arrows before coding. Use implicit routing (no labels). |
| S7 tier-table overflow with 6 task rows | Med | Fix fontSize=10, 0.32" row height. Verify in render loop. |
| Cost envelope $0.30 overly optimistic | Med | This is a proposal for architect review, not a commitment. OPEN callout on S12 asks for gut-check. |
| Kimi K2.6 TTFT can't hit 2s P95 | Med-High | Flagged as S17 ask #3. Architect input desired. |
| Pack abstraction premature for 10-person team | Med | S16 tradeoff card makes this explicit. |

---

## Execution order

1. Edit constants (TOTAL, palette) — 2 min.
2. S2, S6, S12, S15 content swaps — 10 min.
3. S4 OpenRouter rename — 2 min.
4. S5 2 new containers + arrow rewire — 20 min.
5. S7 full rewrite — 30 min.
6. S8 new slide — 30 min.
7. S14 add failure row — 5 min.
8. S16 tradeoff 2-card swap — 10 min.
9. S17 asks edit — 5 min.
10. Footer renumber S9–S17 — 5 min.
11. Build + render — 2 min.
12. Visual QA all 17 slides — 15 min.
13. Fix iteration (expect 2–3 passes on S5, S7, S8) — 30 min.
14. Copy to mount, deliver — 2 min.

**ETA:** ~2.5 hours of focused work. No blockers.

---

## Not doing (explicit scope)

- No changes to title slide (S1) — v0.α still accurate.
- No changes to skeptic state machine (S9) content — only renumber.
- No changes to scoring engine internals (S10) — K2.6 is invisible to that slide's abstraction level.
- No new ELI20 deck update — user asked only for architect deck.
- No code changes to `core/` modules yet — deck is spec; code follows separately.
