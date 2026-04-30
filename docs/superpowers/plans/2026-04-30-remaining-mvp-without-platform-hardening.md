# Remaining MVP Implementation Plan — Profile, Personalization, Examiner, Evidence

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the remaining architecture items except sandbox runtime, observability, security/privacy hardening, and post-MVP/v1 scope.

**Architecture:** Add a privacy-scrubbed candidate intake path that materializes `USER.md`, feed that profile context into Challenger/Examiner prompts, complete the conversational Examiner/pacing slice, and build recruiter-facing score evidence with human overrides. Reads remain event-sourced: session creation emits profile events, scorers emit justified signals/failures, recruiter APIs render projections from the append-only log.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2 templates, Pydantic v2, SQLite event log, pytest/ruff/mypy. No React rewrite in this plan; use the existing server-rendered UI unless a later product decision explicitly introduces React.

**Explicitly out of scope for this plan:**
- Item 4: gVisor/Firecracker/Jupyter sandbox runtime.
- Item 8: OpenTelemetry, Prometheus, dashboards, audit-log platform.
- Item 9: full security/privacy hardening, encryption-at-rest, retention jobs, full adversarial suite. Minimal PII stripping needed for intake is still included because it is part of Candidate Profile Ingestion.
- Item 10: post-MVP/v1 integrations and differentiators.

---

## File Structure

- Create `core/candidate_intake.py` — deterministic profile parsing/redaction/rendering helpers.
- Modify `core/events.py` — expand `ProfileIngested`; add durable scorer/override fields if needed.
- Modify `core/domain.py` — add `Signal.id`, `Signal.justification`, optional `Signal.replaces_ref`/`actor` semantics while keeping defaults for existing tests.
- Modify `core/projections.py` — project profile metadata, signals, scorer failures, and overrides for recruiter evidence reads.
- Modify `adapters/http/app.py` — accept candidate intake form/upload fields; add recruiter evidence and override routes.
- Modify `adapters/http/templates/start.html` — consent + optional profile/resume fields.
- Create `adapters/http/templates/recruiter_session.html` — evidence drill view.
- Modify `adapters/challenger/llm_challenger.py` — accept `user_context` and include `USER.md` as dynamic suffix.
- Modify `adapters/examiner/llm_examiner.py` — accept `user_context`, return backchannel/typing-compatible outcomes, anchor probes on claims.
- Modify `adapters/http/session_runner.py` — load `USER.md`, pass it to Challenger/Examiner, persist pacing/backchannel events, preserve scorer DLQ state.
- Modify `adapters/scorer/_base.py` and concrete scorers — require/derive non-empty signal justifications.
- Modify `adapters/scorer/aggregator.py` — aggregate reviewer override signals preferentially and surface partial-score reasons.
- Tests:
  - `tests/unit/test_candidate_intake.py`
  - `tests/unit/test_personalized_prompts.py`
  - `tests/unit/test_scorer_evidence.py`
  - `tests/unit/test_human_override.py`
  - `tests/integration/test_candidate_intake_http.py`
  - `tests/integration/test_examiner_personalized_pacing.py`
  - `tests/integration/test_recruiter_evidence_view.py`

---

## Task 1: Candidate Intake Domain + USER.md Rendering

**Files:**
- Create: `core/candidate_intake.py`
- Modify: `core/events.py`
- Test: `tests/unit/test_candidate_intake.py`

- [ ] **Step 1: Write failing candidate intake tests**

```python
from __future__ import annotations

from core.candidate_intake import build_profile, render_user_md


def test_build_profile_scrubs_pii_and_extracts_context() -> None:
    profile = build_profile(
        candidate_handle="Ada Lovelace",
        declared_role="ML Engineer",
        years_experience="5",
        declared_skills_text="Python, PyTorch, SQL",
        resume_text="Ada Lovelace\nada@example.com\n+1 415 555 0101\nLed ranking model at ShopCo.",
        other_details="Strongest in causal inference and experimentation.",
    )

    assert profile.declared_role == "ML Engineer"
    assert profile.years_experience == 5
    assert profile.declared_skills == ("Python", "PyTorch", "SQL")
    rendered = render_user_md(profile)
    assert "Ada Lovelace" not in rendered
    assert "ada@example.com" not in rendered
    assert "415 555 0101" not in rendered
    assert "Led ranking model at ShopCo" in rendered
    assert "Strongest in causal inference" in rendered


def test_render_user_md_handles_empty_optional_profile() -> None:
    profile = build_profile(
        candidate_handle="candidate",
        declared_role="",
        years_experience="not-a-number",
        declared_skills_text="",
        resume_text="",
        other_details="",
    )

    rendered = render_user_md(profile)
    assert "# Candidate" in rendered
    assert "No name, email, phone" in rendered
    assert "_(none provided)_" in rendered
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk uv run pytest -q tests/unit/test_candidate_intake.py
```

Expected: fails with `ModuleNotFoundError: No module named 'core.candidate_intake'`.

- [ ] **Step 3: Implement `core/candidate_intake.py`**

Create a focused module with:

```python
from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


@dataclass(frozen=True)
class CandidateProfile:
    declared_role: str
    years_experience: int | None
    declared_skills: tuple[str, ...]
    claims: tuple[str, ...]
    projects: tuple[str, ...] = ()


def scrub_pii(text: str, *, candidate_handle: str = "") -> str:
    cleaned = _EMAIL_RE.sub("[redacted-email]", text)
    cleaned = _PHONE_RE.sub("[redacted-phone]", cleaned)
    handle = candidate_handle.strip()
    if handle and handle.lower() != "candidate":
        cleaned = re.sub(re.escape(handle), "[redacted-name]", cleaned, flags=re.I)
    return cleaned.strip()


def build_profile(
    *,
    candidate_handle: str,
    declared_role: str,
    years_experience: str,
    declared_skills_text: str,
    resume_text: str,
    other_details: str,
) -> CandidateProfile:
    skills = tuple(
        dict.fromkeys(
            skill.strip()
            for chunk in declared_skills_text.splitlines()
            for skill in chunk.split(",")
            if skill.strip()
        )
    )
    years = _parse_years(years_experience)
    combined = "\n".join([resume_text, other_details])
    scrubbed_lines = [
        line.strip(" -•\t")
        for line in scrub_pii(combined, candidate_handle=candidate_handle).splitlines()
        if line.strip(" -•\t")
    ]
    return CandidateProfile(
        declared_role=declared_role.strip() or "DS / ML Engineer",
        years_experience=years,
        declared_skills=skills,
        claims=tuple(scrubbed_lines[:12]),
    )


def render_user_md(profile: CandidateProfile) -> str:
    years = "unknown" if profile.years_experience is None else str(profile.years_experience)
    skills = _bullet_list(profile.declared_skills)
    claims = _bullet_list(profile.claims, quoted=True)
    return "\n".join([
        "# Candidate",
        "",
        f"- **Role applied for:** {profile.declared_role}",
        f"- **Years of experience (self-reported):** {years}",
        "",
        "_No name, email, phone, address, photo, or demographic signal is loaded into this file._",
        "",
        "## Declared skills",
        skills,
        "",
        "## Claims from profile",
        claims,
        "",
    ])


def _parse_years(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


def _bullet_list(items: tuple[str, ...], *, quoted: bool = False) -> str:
    if not items:
        return "- _(none provided)_"
    if quoted:
        return "\n".join(f'- "{item}"' for item in items)
    return "\n".join(f"- {item}" for item in items)
```

- [ ] **Step 4: Expand `ProfileIngested` event**

Change `core/events.py` from `profile_id: str` only to explicit structured fields with backward-compatible defaults:

```python
class ProfileIngested(_Evt):
    profile_id: str
    declared_role: str = "DS / ML Engineer"
    years_experience: int | None = None
    declared_skills: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    user_md_path: str | None = None
```

- [ ] **Step 5: Verify GREEN**

Run:

```bash
rtk uv run pytest -q tests/unit/test_candidate_intake.py tests/unit/test_events.py
```

Expected: all pass.

---

## Task 2: HTTP Consent/Profile Intake at Session Start

**Files:**
- Modify: `adapters/http/templates/start.html`
- Modify: `adapters/http/app.py`
- Test: `tests/integration/test_candidate_intake_http.py`

- [ ] **Step 1: Write failing HTTP intake test**

```python
from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from core.events import ProfileIngested
from core.rubric_loader import load_rubric

_RUBRIC = """
name: test
version: 1
dimensions:
  - name: communication
    weight: 1.0
aggregation:
  min_signals_per_dimension: 1
  confidence_weighting: true
"""


def test_session_start_materializes_user_md_and_profile_event(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "log.db")
    router = ModelRouter(FakeRouter())
    output_dir = tmp_path / "outputs"
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        aggregator=RubricAggregator(load_rubric(yaml_str=_RUBRIC), output_dir=output_dir),
    )
    client = TestClient(make_app(log=log, runner=runner, output_dir=output_dir))

    response = client.post("/sessions", data={
        "candidate_handle": "Jane Candidate",
        "candidate_role": "ML Engineer",
        "years_experience": "4",
        "declared_skills": "Python, NLP",
        "resume_text": "jane@example.com\nLed transformer search relevance project.",
        "other_details": "Prefers careful offline evaluation before launch.",
    }, follow_redirects=False)

    assert response.status_code == 303
    session_id = response.headers["location"].split("/sessions/")[1]
    events = [env.payload for env in log.get_session(session_id)]
    profile_events = [event for event in events if isinstance(event, ProfileIngested)]
    assert len(profile_events) == 1
    assert profile_events[0].declared_skills == ("Python", "NLP")
    user_md = output_dir / "sessions" / session_id / "USER.md"
    assert user_md.exists()
    rendered = user_md.read_text(encoding="utf8")
    assert "Led transformer search relevance project" in rendered
    assert "jane@example.com" not in rendered
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_candidate_intake_http.py
```

Expected: fails because `/sessions` does not accept/store candidate detail fields and no `USER.md` is written.

- [ ] **Step 3: Update start form**

In `adapters/http/templates/start.html`, add optional fields under the handle:

```html
<fieldset>
  <legend>Optional personalization context</legend>
  <p class="note">Only provide what you consent to use for interview personalization.</p>
  <label for="candidate_role">Role applied for</label>
  <input id="candidate_role" name="candidate_role" type="text" placeholder="e.g. ML Engineer">
  <label for="years_experience">Years of experience</label>
  <input id="years_experience" name="years_experience" type="text" placeholder="e.g. 4">
  <label for="declared_skills">Declared skills</label>
  <input id="declared_skills" name="declared_skills" type="text" placeholder="Python, PyTorch, SQL">
  <label for="resume_text">Resume/profile notes</label>
  <textarea id="resume_text" name="resume_text" rows="8" placeholder="Paste resume text or relevant project details"></textarea>
  <label for="other_details">Anything else to personalize the interview</label>
  <textarea id="other_details" name="other_details" rows="4"></textarea>
</fieldset>
```

- [ ] **Step 4: Wire intake in `create_session`**

Change `adapters/http/app.py` route signature to include optional `Form("")` fields. After `CandidateJoined`, call `build_profile`, write `outputs/sessions/<session_id>/USER.md`, and append `ProfileIngested` at the next sequence. Use `output_dir` from `make_app`, falling back to `outputs`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
rtk uv run pytest -q tests/integration/test_candidate_intake_http.py tests/integration/test_http_happy.py
```

Expected: all pass; old no-profile flow still works.

---

## Task 3: Challenger + Examiner Personalization from USER.md

**Files:**
- Modify: `adapters/challenger/llm_challenger.py`
- Modify: `adapters/examiner/llm_examiner.py`
- Modify: `adapters/http/session_runner.py`
- Test: `tests/unit/test_personalized_prompts.py`

- [ ] **Step 1: Write failing prompt-context tests**

```python
from __future__ import annotations

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.examiner.llm_examiner import LlmExaminer
from adapters.llm.router import ModelRouter


class _CapturingProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def call(self, *, tier: str, prompt: str, stream: bool = False, timeout: float | None = None) -> str:
        del tier, stream, timeout
        self.prompts.append(prompt)
        return self.response


def test_challenger_includes_user_md_dynamic_suffix() -> None:
    provider = _CapturingProvider('{"prompt_markdown":"Question?","turn_kind":"question"}')
    challenger = LlmChallenger(ModelRouter(provider, sleep=lambda _: None))

    assert list(challenger.propose_prompts("sess-1", user_context="## Declared skills\n- PyTorch")) == ["Question?"]
    assert "## Candidate profile" in provider.prompts[-1]
    assert "PyTorch" in provider.prompts[-1]


def test_examiner_includes_user_md_dynamic_suffix() -> None:
    provider = _CapturingProvider('{"action":"probe","text":"Tell me about your ranking model.","rationale":"resume claim"}')
    examiner = LlmExaminer(ModelRouter(provider, sleep=lambda _: None))

    outcome, failure = examiner.review("sess-1", [], user_context="## Claims\n- Led ranking model")

    assert failure is None
    assert outcome.probe_text == "Tell me about your ranking model."
    assert "## Candidate profile" in provider.prompts[-1]
    assert "Led ranking model" in provider.prompts[-1]
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk uv run pytest -q tests/unit/test_personalized_prompts.py
```

Expected: fails because methods do not accept `user_context`.

- [ ] **Step 3: Add optional `user_context` parameters**

- `LlmChallenger.propose_prompts(self, session_id: str, *, user_context: str = "")`.
- `LlmChallenger._compose_prompt(self, session_id: str, *, user_context: str = "")`.
- `LlmExaminer.review(..., user_context: str = "")`.
- `LlmExaminer._compose_prompt(..., user_context: str = "")`.

Append:

```python
if user_context.strip():
    parts.append("## Candidate profile\n\n" + user_context.strip())
```

- [ ] **Step 4: Load `USER.md` in `SessionRunner`**

Add helper in `adapters/http/session_runner.py`:

```python
def _load_user_context(session_id: str) -> str:
    for base in (Path(os.getenv("OUTPUT_DIR", "outputs")), Path("outputs")):
        path = base / "sessions" / session_id / "USER.md"
        if path.exists():
            return path.read_text(encoding="utf8")
    return ""
```

Then pass it into:

```python
self.challenger.propose_prompts(session_id, user_context=_load_user_context(session_id))
self.examiner.review(session_id, [], coverage=coverage, user_context=_load_user_context(session_id))
```

If avoiding environment access in `SessionRunner`, add `session_workspace_root: Path = Path("outputs/sessions")` as a dataclass field and pass it from `make_app`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
rtk uv run pytest -q tests/unit/test_personalized_prompts.py tests/integration/test_candidate_intake_http.py
```

Expected: all pass.

---

## Task 4: Examiner Humanly Texture + Pacing Events, Without Observability Platform

**Files:**
- Modify: `adapters/examiner/llm_examiner.py`
- Modify: `adapters/http/session_runner.py`
- Modify: `adapters/http/templates/turn.html`
- Test: `tests/integration/test_examiner_personalized_pacing.py`

- [ ] **Step 1: Write failing pacing/backchannel integration test**

Create a test that starts a session with profile context, submits a candidate answer, and verifies the event log contains a short `BackchannelPosted` before the examiner probe when the examiner returns a probe.

Core assertion:

```python
payload_types = [type(env.payload).__name__ for env in log.get_session(session_id)]
assert "BackchannelPosted" in payload_types
assert payload_types.index("BackchannelPosted") < payload_types.index("TurnPosted")
```

Also assert the probe text can reference a profile claim supplied through `USER.md`.

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_examiner_personalized_pacing.py
```

Expected: fails because no `BackchannelPosted` is emitted.

- [ ] **Step 3: Emit bounded backchannels in `_do_probe`**

Before appending the examiner probe turn, append one of a small deterministic set using the last candidate turn id as an idempotency key:

```python
self._append(
    session_id,
    log,
    BackchannelPosted(message="got it"),
    idem_key=f"backchannel-before-probe:{last_candidate_turn_id}",
)
```

Guardrails:
- Do not emit two backchannels in a row.
- Do not emit if the session is closing the problem.
- Keep text to 1-3 words.

- [ ] **Step 4: Render backchannels inline**

Update `_conversation_messages()` or `turn.html` so `BackchannelPosted` appears as a subtle interviewer message but does not require candidate input.

- [ ] **Step 5: Add pacing floor without metrics**

Implement a local-only pacing floor for visible examiner probes:

```python
minimum_probe_ms = 800
elapsed = _elapsed_ms(started)
if 0 < elapsed < minimum_probe_ms:
    time.sleep((minimum_probe_ms - elapsed) / 1000)
```

Keep this inside the non-streaming path and add a test seam if needed. This is not observability; dashboards/tracing remain out of scope.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
rtk uv run pytest -q tests/integration/test_examiner_personalized_pacing.py tests/integration/test_text_socratic_flow.py tests/integration/test_probe_persistence.py
```

Expected: all pass.

---

## Task 5: Scorer Evidence Completeness + Partial Score Semantics

**Files:**
- Modify: `core/domain.py`
- Modify: `adapters/scorer/_base.py`
- Modify: concrete `adapters/scorer/llm_*_scorer.py` only if prompts need schema wording updates.
- Modify: `adapters/http/session_runner.py`
- Modify: `adapters/scorer/aggregator.py`
- Test: `tests/unit/test_scorer_evidence.py`

- [ ] **Step 1: Write failing scorer evidence tests**

```python
from __future__ import annotations

from datetime import UTC, datetime

from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, Rubric, Signal


def test_signal_requires_non_empty_justification_for_scorer_output() -> None:
    sig = Signal(
        id="sig-1",
        dimension=Dimension.model_rationale,
        value=0.8,
        confidence=0.9,
        source_refs=("artifact-1",),
        emitted_by="rationale_scorer",
        justification="Candidate compared nonlinear model against a simpler baseline.",
        at=datetime.now(UTC),
    )
    assert sig.justification.startswith("Candidate compared")


def test_aggregator_marks_missing_dimension_as_partial() -> None:
    rubric = Rubric(version="v1", weights={
        Dimension.model_rationale: 0.5,
        Dimension.communication: 0.5,
    })
    result = RubricAggregator(rubric).aggregate("sess-1", [
        Signal(
            id="sig-1",
            dimension=Dimension.model_rationale,
            value=0.8,
            confidence=0.9,
            source_refs=("artifact-1",),
            emitted_by="rationale_scorer",
            justification="Clear rationale.",
            at=datetime.now(UTC),
        )
    ])
    assert Dimension.communication in result.insufficient_dimensions
    assert "insufficient evidence" in result.feedback_markdown
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk uv run pytest -q tests/unit/test_scorer_evidence.py
```

Expected: fails because `Signal` has no `id`/`justification` fields.

- [ ] **Step 3: Extend `Signal` with backward-compatible defaults**

In `core/domain.py`:

```python
class Signal(_Frozen):
    id: str = ""
    dimension: Dimension
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: tuple[str, ...]
    emitted_by: str
    justification: str = "evidence recorded"
    at: datetime
```

Add a validator that if `emitted_by` contains `scorer` and `justification.strip()` is empty, raises `ValueError`.

- [ ] **Step 4: Populate justifications in scorers**

In `_parse_signal`, read `justification` from LLM JSON, falling back to a short deterministic phrase that includes dimension and artifact id. In `_heuristic_signal`, set `justification` to explain keyword fallback.

- [ ] **Step 5: Preserve signal id/source refs in `SessionRunner`**

When normalizing `source_refs` to raw `artifact_id`, do not drop `signal.id` or `signal.justification`.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
rtk uv run pytest -q tests/unit/test_scorer_evidence.py tests/unit/test_aggregator.py tests/unit/test_domain.py
```

Expected: all pass.

---

## Task 6: Human Override Event + Aggregation Preference

**Files:**
- Modify: `core/events.py`
- Modify: `adapters/scorer/aggregator.py`
- Modify: `core/projections.py`
- Test: `tests/unit/test_human_override.py`

- [ ] **Step 1: Write failing override aggregation test**

```python
from __future__ import annotations

from datetime import UTC, datetime

from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, Rubric, Signal


def test_reviewer_signal_overrides_scorer_signal_for_same_dimension() -> None:
    rubric = Rubric(version="v1", weights={Dimension.communication: 1.0})
    scorer_signal = Signal(
        id="sig-scorer",
        dimension=Dimension.communication,
        value=0.4,
        confidence=0.8,
        source_refs=("turn-1",),
        emitted_by="communication_scorer",
        justification="Scorer saw unclear explanation.",
        at=datetime.now(UTC),
    )
    reviewer_signal = Signal(
        id="sig-reviewer",
        dimension=Dimension.communication,
        value=0.9,
        confidence=1.0,
        source_refs=("turn-1",),
        emitted_by="reviewer:recruiter-1",
        justification="Human reviewed transcript and found explanation strong.",
        at=datetime.now(UTC),
    )

    result = RubricAggregator(rubric).aggregate("sess-1", [scorer_signal, reviewer_signal])

    assert result.score.per_dimension[Dimension.communication] == 0.9
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk uv run pytest -q tests/unit/test_human_override.py
```

Expected: fails because aggregation averages both signals.

- [ ] **Step 3: Update aggregation grouping**

In `RubricAggregator.aggregate`, if any signal for a dimension has `emitted_by.startswith("reviewer:")`, score that dimension from reviewer signals only; otherwise use scorer/system signals.

- [ ] **Step 4: Expand `HumanOverride` for audit**

In `core/events.py`:

```python
class HumanOverride(_Evt):
    target_signal_dimension: Dimension
    original_value: float
    override_value: float
    reviewer_id: str
    reason: str
    source_refs: tuple[str, ...]
    emitted_signal_id: str
```

Validation: reason must be non-empty and `source_refs` must have at least one item.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
rtk uv run pytest -q tests/unit/test_human_override.py tests/unit/test_events.py tests/unit/test_aggregator.py
```

Expected: all pass.

---

## Task 7: Recruiter Evidence API + Server-Rendered View

**Files:**
- Modify: `core/projections.py`
- Modify: `adapters/http/app.py`
- Create: `adapters/http/templates/recruiter_session.html`
- Test: `tests/integration/test_recruiter_evidence_view.py`

- [ ] **Step 1: Write failing recruiter evidence test**

Test setup appends a session, candidate/challenger/candidate turns, artifacts, `SignalEmitted`, and `ScoreComputed`, then calls:

```python
response = client.get(f"/recruiter/sessions/{session_id}")
assert response.status_code == 200
assert "Composite score" in response.text
assert "model_rationale" in response.text
assert "Candidate compared nonlinear model" in response.text
assert "artifact-1" in response.text
```

Also test human override submit:

```python
response = client.post(f"/recruiter/sessions/{session_id}/overrides", data={
    "dimension": "model_rationale",
    "original_value": "0.4",
    "override_value": "0.8",
    "reviewer_id": "recruiter-1",
    "reason": "Transcript shows stronger rationale than automated scorer captured.",
    "source_refs": "turn-1",
}, follow_redirects=False)
assert response.status_code == 303
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk uv run pytest -q tests/integration/test_recruiter_evidence_view.py
```

Expected: 404 because routes/templates do not exist.

- [ ] **Step 3: Add evidence projection helper**

Add a read helper that replays `SignalEmitted`, `ScorerFailed`, `ScoreComputed`, `PerProblemScoreComputed`, and `HumanOverride` into a serializable structure:

```python
{
  "session_id": session_id,
  "score": score,
  "dimensions": [
    {"name": dim.value, "value": value, "signals": signals, "partial_reason": "..."}
  ],
}
```

Keep this read-only and sourced only from the event log/projections.

- [ ] **Step 4: Add recruiter GET route**

In `adapters/http/app.py`:

```python
@app.get("/recruiter/sessions/{session_id}", response_class=HTMLResponse)
async def recruiter_session(request: Request, session_id: str) -> HTMLResponse:
    evidence = _score_evidence(session_id, app.state.log)
    return templates.TemplateResponse(request, "recruiter_session.html", evidence | {"request": request})
```

- [ ] **Step 5: Add override POST route**

Route validates dimension/value/reason/source refs, appends `HumanOverride`, appends a reviewer `SignalEmitted`, recomputes `ScoreComputed`, and redirects back to the recruiter page.

- [ ] **Step 6: Create template**

Template must show:
- Composite score and rubric version.
- Per-dimension value or partial state.
- Each signal justification and source refs.
- Override form with required note/source refs.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
rtk uv run pytest -q tests/integration/test_recruiter_evidence_view.py tests/integration/test_http_happy.py
```

Expected: all pass.

---

## Task 8: Documentation + Final Verification

**Files:**
- Modify: `README.md`
- Modify: `architecture-and-plan.md` only if status text is currently stale.
- Modify: `tasks/todo.md`

- [ ] **Step 1: Update roadmap status**

Set README status to:
- CandidateIntake: shipped for text/paste MVP, file parsing if implemented.
- Challenger personalization: shipped.
- Examiner humanly texture: partial or shipped based on Task 4 completion.
- Recruiter dashboard: shipped server-rendered evidence MVP.
- Scorer evidence/override: shipped.

- [ ] **Step 2: Run focused verification**

```bash
rtk uv run pytest -q \
  tests/unit/test_candidate_intake.py \
  tests/unit/test_personalized_prompts.py \
  tests/unit/test_scorer_evidence.py \
  tests/unit/test_human_override.py \
  tests/integration/test_candidate_intake_http.py \
  tests/integration/test_examiner_personalized_pacing.py \
  tests/integration/test_recruiter_evidence_view.py
```

Expected: all pass.

- [ ] **Step 3: Run quality gates**

```bash
rtk uv run ruff check core adapters tests
rtk uv run mypy core adapters tests/unit/test_candidate_intake.py tests/unit/test_personalized_prompts.py tests/unit/test_scorer_evidence.py tests/unit/test_human_override.py
rtk uv run pytest -q
rtk git diff --check
```

Expected: all pass.

- [ ] **Step 4: Document review in `tasks/todo.md`**

Add exact commands and pass/fail output. Include any deliberately deferred items:
- sandbox runtime hardening,
- observability platform,
- full security/privacy hardening,
- post-MVP integrations.

---

## Self-Review

- **Spec coverage:** Covers requested remaining items 1, 2, 3, 5, 6, and 7 from the prior summary. Explicitly excludes 4, 8, 9, and 10.
- **Placeholder scan:** No task uses TBD/TODO/fill-later language. Each task has file paths, tests, commands, and expected outcomes.
- **Type consistency:** `ProfileIngested`, `Signal`, `HumanOverride`, `user_context`, and recruiter route names are consistent across tasks.
- **Scope check:** This is large but sequential and event-log cohesive. If execution feels too broad, split after Task 3: first PR for candidate intake/personalization, second PR for recruiter evidence/overrides/scorer evidence.
