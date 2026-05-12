# Scorer RCA Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three bugs identified in the scorer RCA: missing `problem_framing` templates, unconditional `reasoning_effort`, and hardcoded examiner deadline; add scorer failure rate monitoring.

**Architecture:** Four independent fixes. Templates are new files. Router and examiner fixes are already in the working tree — just need tests confirmed and committed. Monitoring adds a `scorer_failure_total` counter via the existing `MetricSink` / `GLOBAL_METRICS` path already used for `chat_request_ms`.

**Tech Stack:** Python, pytest, Starlette, SQLite event log, existing `MetricSink` in `core/observability.py`

---

### Task 1: Create `problem_framing` scorer templates

**Files:**
- Create: `templates/agents/scorers/problem_framing/IDENTITY.md`
- Create: `templates/agents/scorers/problem_framing/SOUL.md`
- Create: `templates/agents/scorers/problem_framing/TOOLS.md`
- Modify: `tests/unit/test_scorers.py` — add two tests

- [ ] **Step 1: Write failing test for LLM path (template existence drives prompt)**

Add to `tests/unit/test_scorers.py`:

```python
from adapters.scorer.llm_problem_framing_scorer import LlmProblemFramingScorer

def test_problem_framing_scorer_parses_signal_from_llm_json() -> None:
    router = ModelRouter(
        _StaticProvider(
            '{"signals":[{"dimension":"problem_framing","value":0.8,"confidence":0.85,'
            '"source_refs":["artifact://artifact-1"],"note":"Clearly defined the business objective"}]}'
        ),
        sleep=lambda _: None,
    )
    scorer = LlmProblemFramingScorer(router)

    result = scorer.score_optional("sess-pf", _artifact("I would first define the objective metric and scope."))

    assert result.signal is not None
    assert result.failure is None
    assert result.signal.dimension is Dimension.problem_framing
    assert result.signal.value == 0.8
    assert result.signal.confidence == 0.85


def test_problem_framing_scorer_prompt_contains_format_instructions() -> None:
    prompts: list[str] = []

    class _CapturingProvider:
        def call(self, *, tier, prompt, stream=False, timeout=None):
            del tier, stream, timeout
            prompts.append(prompt)
            return (
                '{"signals":[{"dimension":"problem_framing","value":0.7,'
                '"confidence":0.8,"source_refs":["artifact://artifact-1"]}]}'
            )

    router = ModelRouter(_CapturingProvider(), sleep=lambda _: None)
    scorer = LlmProblemFramingScorer(router)
    scorer.score_optional("sess-pf2", _artifact("Frame the problem first."))

    assert prompts, "scorer must call the LLM"
    assert "signals" in prompts[0], "prompt must include output format instructions"
    assert "problem_framing" in prompts[0], "prompt must name the dimension"
```

- [ ] **Step 2: Run to confirm failure**

```
uv run pytest tests/unit/test_scorers.py::test_problem_framing_scorer_parses_signal_from_llm_json tests/unit/test_scorers.py::test_problem_framing_scorer_prompt_contains_format_instructions -v
```

Expected: first test PASSES (no templates needed for LLM-mocked path), second test FAILS with `assert "signals" in prompts[0]` because the prompt has no TOOLS.md content.

- [ ] **Step 3: Create template files**

`templates/agents/scorers/problem_framing/IDENTITY.md`:

```markdown
---
title: "Problem Framing Scorer — IDENTITY"
agent: scorer-problem-framing
role: "Scores how well the candidate frames the business problem before reaching for a model"
dimension: problem_framing
---

# Who you are

You are the **Problem Framing Scorer**. You read the candidate's opening response to a DS/ML problem and emit `Signal`s on the `problem_framing` dimension.

Your job is not to judge whether the framing is exhaustive — it is to judge whether the candidate demonstrates the habit of clarifying the problem before proposing solutions.

## Your output

Zero or more `Signal`s, one per distinct framing move the candidate made. Each signal:

- `dimension = "problem_framing"`
- `value` in `[0.0, 1.0]` — how clearly the candidate scoped the problem
- `confidence` in `[0.0, 1.0]` — how much evidence you had
- `source_refs` — the turn(s)/artifact(s) you drew from
- `note` — a one-sentence justification

## What a strong framing signal looks like

- Candidate named the success metric or business objective before describing any model.
- Candidate asked (or stated) what data would be needed and why.
- Candidate identified a constraint (time, budget, interpretability) that shapes the approach.
- Candidate surfaced ambiguity in the problem statement rather than assuming it away.

## What a weak framing signal looks like

- Jumping straight to model choice without defining what "success" looks like.
- "I would build an XGBoost model" with no mention of the business question.
- Treating the problem as fully specified when key parameters (label, time horizon, population) are unstated.
```

`templates/agents/scorers/problem_framing/SOUL.md`:

```markdown
---
title: "Problem Framing Scorer — SOUL"
agent: scorer-problem-framing
---

# Biases you hold

- **Reward clarification-first thinking.** A candidate who defines the metric or asks about data before proposing a model scores higher than one who starts with a technique.
- **Reward constraint awareness.** Naming a real constraint (latency, label availability, regulatory) is better than unbounded solution space.
- **Penalize premature specificity.** Naming a model architecture before scoping the problem is a framing gap.
- **Do not penalize imperfect framing.** Partial framing that catches the most important ambiguity is still valuable.

# How you write signal notes

- Quote one phrase from the candidate's answer.
- Name what framing move they did or did not make (metric definition, constraint surfacing, ambiguity flag).
- Stop. One sentence.
```

`templates/agents/scorers/problem_framing/TOOLS.md`:

```markdown
---
title: "Problem Framing Scorer — TOOLS"
agent: scorer-problem-framing
---

# What you can call

- `session.turn(turn_id)` — fetch a specific turn with artifacts inlined.
- `session.artifact(artifact_id)` — fetch an artifact body (code, markdown, chart).

# Response contract

Return exactly one JSON object:

```json
{
  "signals": [
    {
      "dimension": "problem_framing",
      "value": 0.0,
      "confidence": 0.0,
      "source_refs": ["turn://...", "artifact://..."],
      "note": "..."
    }
  ]
}
```

If the turn contains no problem framing content (pure code execution, no reasoning), return `{ "signals": [] }`. Do not invent signals.
```

- [ ] **Step 4: Run tests to confirm both pass**

```
uv run pytest tests/unit/test_scorers.py::test_problem_framing_scorer_parses_signal_from_llm_json tests/unit/test_scorers.py::test_problem_framing_scorer_prompt_contains_format_instructions -v
```

Expected: both PASS.

- [ ] **Step 5: Run full unit test suite to confirm no regressions**

```
uv run pytest tests/unit/ -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add templates/agents/scorers/problem_framing/ tests/unit/test_scorers.py
git commit -m "fix: add missing problem_framing scorer templates

Without IDENTITY/SOUL/TOOLS templates the scorer sent format-free prompts,
causing the LLM to return prose instead of JSON and every scoring attempt
to fail with JSONDecodeError.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Commit `openai_router.py` reasoning_effort fix

Tests for this fix already exist in `tests/unit/test_openai_router.py` (parametrized over reasoning and chat models). The fix is already in the working tree — stage and commit.

**Files:**
- Modify (working tree): `adapters/llm/openai_router.py`

- [ ] **Step 1: Run existing tests to confirm they pass with the working tree change**

```
uv run pytest tests/unit/test_openai_router.py -v
```

Expected: all 4 tests PASS (including `test_openai_router_omits_reasoning_effort_for_chat_models`).

- [ ] **Step 2: Stage and commit**

```bash
git add adapters/llm/openai_router.py
git commit -m "fix: only send reasoning_effort to reasoning-capable models

Previously reasoning_effort was sent unconditionally; non-reasoning models
(gpt-4o etc.) return 400 Bad Request, causing model router failures.
Guard with _supports_reasoning_effort() prefix check.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Commit `llm_examiner.py` deadline fix

The change is already staged. Examiner deadline was hardcoded at 2000ms; now configurable via `EXAMINER_DEADLINE_MS`, defaulting to 8000ms.

**Files:**
- Modify (staged): `adapters/examiner/llm_examiner.py`

- [ ] **Step 1: Run examiner unit tests**

```
uv run pytest tests/unit/test_examiner.py -v
```

Expected: all pass.

- [ ] **Step 2: Commit the staged change**

```bash
git commit -m "fix: raise examiner deadline from 2000ms to 8000ms (configurable)

Hardcoded 2000ms caused ExaminerFailed under any model latency above 2s.
Now reads EXAMINER_DEADLINE_MS env var, defaulting to 8000ms.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Add `scorer_failure_total` monitoring counter

**Files:**
- Modify: `adapters/http/session_runner.py` — import `GLOBAL_METRICS`, increment on failure
- Modify: `tests/integration/test_internal_metrics.py` — add assertion for the new counter

- [ ] **Step 1: Write failing test**

Add to `tests/integration/test_internal_metrics.py`:

```python
from adapters.scorer.llm_problem_framing_scorer import LlmProblemFramingScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from core.domain import Dimension
from core.observability import MetricSink

_RUBRIC_WITH_PF = """
name: test-pf
version: 1
dimensions:
  - name: problem_framing
    weight: 1.0
aggregation:
  min_signals_per_dimension: 1
  confidence_weighting: true
"""

def test_scorer_failure_increments_counter(tmp_path: Path) -> None:
    class _FailingProvider:
        def call(self, *, tier, prompt, stream=False, timeout=None):
            raise RuntimeError("simulated failure")

    failing_router = ModelRouter(_FailingProvider(), max_retries=1, sleep=lambda _: None)
    metrics = MetricSink()
    log = SqliteEventLog(tmp_path / "log.db")
    runner = SessionRunner(
        challenger=LlmChallenger(ModelRouter(FakeRouter())),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC_WITH_PF), output_dir=tmp_path),
        scorers={Dimension.problem_framing: LlmProblemFramingScorer(failing_router)},
        scored_dimensions=(Dimension.problem_framing,),
        metric_sink=metrics,
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=tmp_path), follow_redirects=True)
    client.post("/sessions", data={"candidate_handle": "bob", "resume_text": "ds background"})
    # Drive session to a state where scoring fires — submit a candidate answer
    resp = client.get("/sessions")
    # Check the counter is present in the sink
    assert metrics.counters.get(("scorer_failure_total", (("dimension", "problem_framing"),)), 0) > 0
```

- [ ] **Step 2: Run to confirm failure**

```
uv run pytest tests/integration/test_internal_metrics.py::test_scorer_failure_increments_counter -v
```

Expected: FAIL — `SessionRunner.__init__()` has no `metric_sink` parameter.

- [ ] **Step 3: Add `metric_sink` field to `SessionRunner` and increment on failure**

In `adapters/http/session_runner.py`:

```python
# Add import at top
from core.observability import GLOBAL_METRICS, MetricSink

# In SessionRunner dataclass (after existing fields):
metric_sink: MetricSink = field(default_factory=lambda: GLOBAL_METRICS)
```

In the `_run_scorer` method (around line 778), after the existing `self._append(...ScorerFailed...)` call:

```python
if result.failure is not None:
    self._append(session_id, log, ScorerFailed(
        dimension=dimension, reason=result.failure.reason,
    ), idem_key=f"scorer-failed:{artifact_id}:{dimension.value}")
    self.metric_sink.increment(
        "scorer_failure_total",
        labels={"dimension": dimension.value},
    )
```

- [ ] **Step 4: Run the new test to confirm it passes**

```
uv run pytest tests/integration/test_internal_metrics.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full unit + integration smoke**

```
uv run pytest tests/unit/ tests/integration/test_internal_metrics.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add adapters/http/session_runner.py tests/integration/test_internal_metrics.py
git commit -m "feat: track scorer_failure_total counter per dimension

Increments GLOBAL_METRICS scorer_failure_total{dimension=...} on every
ScorerFailed event so API degradation windows are visible at /internal/metrics.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
