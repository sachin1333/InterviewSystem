# Code Update Plan — 3 Design Pillars

**Date:** 2026-04-24
**Scope:** Fold 3 pillars into `InterviewSystem/` codebase.
**Supersedes:** nothing; additive to current Phase α.

---

## Pillars → code mapping

| Pillar | Intent | Primary code touch |
|--------|--------|--------------------|
| 1. Skeptic P95 ≤ 2s | Synchronous candidate-facing LLM reply ≤ 2s P95. | `adapters/llm/router.py`, `adapters/challenger/*`, `adapters/examiner/*`, `adapters/http/session_runner.py` |
| 2. Domain-agnostic + packs | First-class `Pack` abstraction; DS/ML is a pack, not the system. | `core/domain.py`, `core/rubric_loader.py`, `core/session_boot.py`, `templates/packs/*`, `core/events.py` |
| 3. 3-tier router (Kimi K2.6 top) | Swap 2-tier fast/strong → 3-tier cheap/mid/top. Per-task tier map. Per-session cost guardrail. | `adapters/llm/router.py`, `adapters/llm/factory.py`, new `adapters/llm/openrouter_router.py`, new `adapters/llm/task_tier_map.py`, new `adapters/llm/budget.py` |

---

## Current coupling (survey findings)

- Router has 2 tiers: `fast` (5s timeout), `strong` (20s timeout). `JsonModelRouter` Protocol is replicated in 3 adapter files — widening it ripples.
- Only providers: `FakeRouter`, `OpenAIRouter`. No OpenRouter path.
- `domain.Dimension` is a fixed Enum (4 values). Scorers key off it; rubric loader validates against it.
- Rubric YAML already pack-shaped (`name`, `version`, `dimensions`, `aggregation`). Loaded from a fixed path by the harness.
- Template dirs are flat under `templates/agents/{challenger,examiner,scorers/<dim>}`. No per-pack nesting.
- `SessionRunner` takes a router + scorer dict at construction. No pack context; no budget tracker.
- No latency instrumentation. No cost instrumentation.

---

## Phase β — Latency pillar (Pillar 1)

**Goal:** Skeptic-facing LLM call returns first token ≤ 600ms P95, full JSON ≤ 2s P95.

### β.1 Deadline propagation

- [ ] Add `deadline_ms: int | None` to `ModelRouter.call` + `call_json`. Overrides tier default.
- [ ] Update `JsonModelRouter` Protocol in all 3 adapter call-sites (`scorer/_base.py`, `challenger/llm_challenger.py`, `examiner/llm_examiner.py`). Additive (default None).
- [ ] `LlmChallenger.draft_challenge()` passes `deadline_ms=2000`. `LlmExaminer` probe: `deadline_ms=2000`. Scorer: unchanged (off critical path).

### β.2 Streaming TTFT

- [ ] `OpenAIRouter.call(stream=True)` already exists. Add `call_json_stream()` on `ModelRouter` that streams tokens, accumulates, parses JSON when closing brace balances.
- [ ] `LlmChallenger.draft_challenge_stream()` variant for `SessionRunner` to forward WS chunks to candidate UI.
- [ ] HTTP handler: send first chunk within 600ms else emit `ChallengerFailed(reason="ttft_breach")` and use canned fallback (existing `canned_prompts_path`).

### β.3 Latency instrumentation

- [ ] New `adapters/llm/latency.py`: in-process P50/P95 estimator per tier + per task. Sliding window 1000 calls.
- [ ] Router emits `llm_latency` log line per call (`tier`, `task`, `wall_ms`, `ttft_ms`, `tokens_in`, `tokens_out`, `outcome`).
- [ ] Test harness `tools/latency_probe.py`: run 200 synthetic challenger drafts, report P50/P95/P99, assert P95 ≤ 2s.

### β.4 Move scoring explicitly off critical path

- [ ] Confirm: `SessionRunner.advance` already ends at `RequestCandidateInput` / `EndSession`. Scoring is triggered separately. **Already async** → no change.
- [ ] Add assert test: `RequestScoring` never blocks candidate-visible response.

**Acceptance:** `tools/latency_probe.py` reports P95 ≤ 2000ms over 200 real (OpenRouter + FakeRouter) runs.

---

## Phase γ — 3-tier router (Pillar 3)

**Goal:** Swap `fast/strong` → `cheap/mid/top`; Kimi K2.6 default top; per-task tier mapping; per-session budget.

### γ.1 Tier widening — breaking but localized

- [ ] Edit `adapters/llm/router.py`:
  - `Tier = Literal["cheap", "mid", "top"]`.
  - `cheap_timeout=2.5`, `mid_timeout=6.0`, `top_timeout=15.0` (top gets budget — streaming keeps UI happy).
  - Fallback chain: `top` fails → `mid`; `mid` fails → `cheap`; `cheap` fails → placeholder.
  - Legacy-alias helper: `alias("fast")→"cheap"`, `alias("strong")→"top"` for migration. Remove after all callers updated.
- [ ] Update Protocol in scorer/challenger/examiner (3 files).
- [ ] Update `fake_router.py` to accept 3 tiers.

### γ.2 OpenRouter provider

- [ ] New `adapters/llm/openrouter_router.py`. HTTPS client talking to `https://openrouter.ai/api/v1/chat/completions`. Auth via `OPENROUTER_API_KEY`.
- [ ] Per-tier model selection via env:
  - `OPENROUTER_TOP_MODEL` default `moonshotai/kimi-k2.6`
  - `OPENROUTER_MID_MODEL` default `deepseek/deepseek-v3`
  - `OPENROUTER_CHEAP_MODEL` default `meta-llama/llama-3.1-8b-instruct`
- [ ] Stream + non-stream paths match `OpenAIRouter` shape.
- [ ] Response includes `usage` (tokens in/out) — use for cost.

### γ.3 Task → tier map

- [ ] New `adapters/llm/task_tier_map.py`. Explicit table:

| Task | Default tier | Rationale |
|------|--------------|-----------|
| `challenger.draft` | `top` | First-impression quality; skeptic must be sharp. |
| `examiner.probe` | `top` | Same; mid-session follow-up. |
| `scorer.rationale` | `top` | Rubric grading needs defensible reasoning. |
| `scorer.communication` | `mid` | Fluency check, not creative. |
| `scorer.insight_interp` | `top` | Must cite notebook output correctly. |
| `scorer.problem_framing` | `mid` | Classify + short justification. |
| `intake.resume_parse` | `cheap` | Pure extraction. |
| `guardrail.json_repair` | `cheap` | Deterministic fix-up. |

- [ ] Table overridable via `config/tier_overrides.yaml`. Loader validates task names.
- [ ] Every LLM call site passes a `task: TaskKey` argument. Router looks up tier.

### γ.4 Budget guardrail

- [ ] New `adapters/llm/budget.py`: `SessionBudget` dataclass. Tracks cost accrual keyed by `session_id`. Pricing table per `(provider, model)` in $/1M tokens.
- [ ] Router.call takes optional `budget: SessionBudget`. On each call, debit est. cost (`tokens_out * price_out + tokens_in * price_in`).
- [ ] On breach (default `$0.40/session`), router returns `BudgetExceeded` sentinel; `SessionRunner` emits `ChallengerFailed(reason="budget_exceeded")` and uses canned fallback. Event-logged; audit-visible.
- [ ] HTTP middleware injects `SessionBudget` tied to `session_id`.

### γ.5 Retire aliases

- [ ] After all call sites pass explicit tier names, delete alias helper.
- [ ] Update tests.

**Acceptance:**
- Unit: task-tier map covers every LLM call site. Grep audit: no bare `"strong"` or `"fast"` strings in production code.
- Integration: `BudgetExceeded` on artificial low budget triggers fallback path.
- Cost test: 10 simulated sessions under realistic load avg ≤ $0.30/session.

---

## Phase δ — Domain packs (Pillar 2)

**Goal:** `Pack` is first-class. DS/ML migrates to `templates/packs/ds-ml-v1/`. New pack can ship without touching `core/`.

### δ.1 Pack layout

New directory structure:
```
templates/packs/
  ds-ml-v1/
    pack.yaml                  # id, version, display_name, default_tier_overrides
    rubric.yaml                # moved from templates/rubrics/ds-ml-engineer-v1.yaml
    challenger/
      IDENTITY.md SOUL.md TOOLS.md
    examiner/
      IDENTITY.md PACING.md SOUL.md TOOLS.md
    scorers/
      <dimension>/IDENTITY.md PROMPT.md
    notebook_boilerplate.ipynb # optional; per-pack starter
  backend-swe-v1/
    ... (placeholder — Phase ε)
  product-analyst-v1/
    ... (placeholder — Phase ε)
```

- [ ] Move existing files under `templates/packs/ds-ml-v1/`.
- [ ] Keep thin back-compat shim in `templates/rubrics/` pointing at new location (or delete if no tests reference it — check).

### δ.2 Pack loader

- [ ] New `core/pack_loader.py`:
  - `Pack` dataclass: `id: str`, `version: str`, `display_name: str`, `rubric: domain.Rubric`, `template_paths: dict[role, Path]`, `tier_overrides: dict[TaskKey, Tier]`.
  - `load_pack(pack_id: str) -> Pack`.
  - Validates `pack.yaml` schema.
- [ ] Pack registry: `PackRegistry` — lazy-load, cache by id.

### δ.3 Dimension dynamism (minimal)

Two options, pick one:

- **A. Keep Dimension enum, restrict rubrics to subset.** Pack can only declare dims ∈ existing enum. Smallest change. Prevents new-dimension packs but unblocks non-DS-specific wording via templates.
- **B. Open Dimension to string-typed NewType.** Pack declares any dims; scorer must exist for each. Touches every file that imports Dimension (~12 files).

**Lean: A for this slice.** B deferred to Phase ε. Rationale: 4 dims (framing, rationale, insight_interp, communication) translate across domains via pack-local prompts. "problem_framing" means different things per domain but carries same signal shape.

- [ ] No Dimension code change. Add `pack.dim_display_name[dim]` string map in pack.yaml for UI rendering.

### δ.4 Session → pack linkage

- [ ] Add `pack_id: str` to `SessionCreated` event. Default fallback: `"ds-ml-v1"` for legacy sessions (back-compat shim at projection time).
- [ ] `SessionStore` carries `pack_id` field. `SessionRunner.advance` loads pack via registry, passes templates_root to challenger/examiner/scorers.
- [ ] HTTP create-session handler accepts `pack_id` param. Validates against registry.

### δ.5 Pack eval harness

- [ ] `tools/pack_eval.py`: given pack + golden-set YAML (pairs of `(candidate_notebook, expected_signals)`), run full pipeline, compare.
- [ ] Per-pack golden set lives at `templates/packs/<id>/eval/golden.yaml`.
- [ ] CI target: `ruff + mypy + pytest + pack_eval` per pack.

**Acceptance:**
- Can run 1 session against `ds-ml-v1` (regression: existing tests pass against moved templates).
- Can run 1 session against a minimal `backend-swe-v1` stub pack without any `core/` change.

---

## Phase ε — New packs + opening Dimension (out of scope for this slice)

- Build `backend-swe-v1`, `product-analyst-v1`.
- Open `Dimension` to string NewType (option B above) if pack authors request non-overlapping dims.

---

## Cross-cutting

### Events touched

- [ ] `core/events.py`: add `pack_id` to `SessionCreated`. Add `BudgetExceeded(reason, est_cost_usd)`. Add `TierFallback(from_tier, to_tier, reason)`.
- [ ] Envelope versioning: bump `events.py` event registry version.

### Config

- [ ] New `config/` dir at repo root:
  - `config/models.yaml` — tier → model + pricing table.
  - `config/tier_overrides.yaml` — task → tier table.
  - `config/budgets.yaml` — per-pack session budget.

### Testing

| Test type | Scope |
|-----------|-------|
| Unit | router tier widening, task-tier lookup, budget debit, pack loader schema. |
| Integration | end-to-end session on `ds-ml-v1` — latency + cost assertions. |
| Regression | existing Phase α tests must still pass after template move. |
| Eval | `pack_eval.py` vs golden set (≥ 0.85 agreement with human baseline). |

### Lessons to capture

- Expect `tasks/lessons.md` updates for:
  - Protocol-widening gotcha (3 adapter files replicate it).
  - Dimension enum vs string tradeoff rationale.
  - K2.6 streaming JSON edge cases (partial objects).

---

## Execution order

```
β.1 deadline plumbing        ─┐
β.2 streaming TTFT            │  1 week
β.3 latency instrument        │
β.4 async-scoring assert      ─┘
─────────────────────────────
γ.1 tier widening            ─┐
γ.2 openrouter provider       │
γ.3 task-tier map             │  1 week
γ.4 budget guardrail          │
γ.5 alias retirement          ─┘
─────────────────────────────
δ.1 pack layout move         ─┐
δ.2 pack loader               │
δ.3 dimension dynamism (A)    │  1.5 weeks
δ.4 session→pack linkage      │
δ.5 pack eval harness         ─┘
```

**Total: ~3.5 weeks single-dev.** Phases parallelizable to ~2 weeks with 2 devs (β + γ + δ all largely independent surface).

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| K2.6 TTFT > 600ms P95 | High | Streaming + canned fallback on breach. Measure early (end of β.2). |
| OpenRouter rate-limits on top tier | Med | Mid-tier fallback (γ.1). Budget tracker reveals cost before abuse. |
| Protocol-widening breaks downstream | Med | Additive new kwargs only. Keep alias helper until γ.5. |
| Pack move breaks existing tests | Low | Shim old paths; grep tests for hardcoded paths first. |
| Dimension-A locks us in | Med | Phase ε reopens via option B. Document lock-in in lessons.md. |
| Budget guardrail false-positive ends session mid-conversation | Med-High | Guardrail returns `BudgetExceeded` → canned fallback, not session kill. UX preserves. Audit-log every breach. |

---

## Out of scope

- No Postgres (SQLite stays).
- No multi-role within a pack (one pack = one role; multi-role is pack-composition, deferred).
- No recruiter dashboard.
- No prompt-injection hardening beyond current NeMo sketch.
- No non-text modalities.
- No model fine-tuning — router + prompting only.
