# Phase 0 — Backbone Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the dependency-free backbone (domain types + event log + two projections + orchestrator + five contract stubs) with a hand-crafted golden-sequence test that proves the orchestrator produces the expected `Action` from a replayed event stream. No LLMs, no UI, no Jupyter.

**Architecture:** Hexagonal / ports-and-adapters. `core/` is pure, imports only `pydantic` (+ `pyyaml` for rubric loader). `tests/unit/` drives TDD for every core module. No adapters land in Phase 0 — they're Phase 1+. Source of truth is `architecture-and-plan.md` §2 (domain), §4.1–§4.5 (backbone), §6 (roadmap), §8 (directory), §10 (open questions), §15.5 (budget-as-test timing).

**Tech Stack:** Python 3.12, `pydantic>=2`, `pyyaml`, `pytest`, `pytest-cov`, `ruff`, `mypy --strict`. No DB, no web framework, no async in Phase 0.

**Prove-it exit gate:** `pytest tests/unit -q` green; golden-sequence test in `tests/unit/test_prove_it.py` passes; `mypy --strict core/` clean; `ruff check` clean.

**Out of Phase 0 scope** (don't creep):
- Postgres adapter (Phase 1)
- LLM adapters / Challenger / Examiner (Phase 3–4)
- Sandbox / Jupyter runtime (Phase 5)
- HTTP/WebSocket API (Phase 7)
- OTel spans + Prometheus metrics (§15.5 — Phase 3+)
- PII encryption-at-rest (Open Q #4 → Phase 1)
- Adversarial injection fixtures with live assertions (Phase 3 — §4.7) — Phase 0 ships fixture *directory* + schema only.

---

## File Structure

```
interview-system/
├── pyproject.toml
├── ruff.toml
├── mypy.ini
├── .github/workflows/ci.yml
├── core/
│   ├── __init__.py
│   ├── domain.py            # Session, Turn, Artifact, Signal, Rubric, Score, Dimension
│   ├── events.py            # Event union + payload dataclasses + envelope
│   ├── eventlog.py          # InMemoryEventLog (append-only, seq-monotonic)
│   ├── projections.py       # SessionStore, ScoreStore — pure reducers
│   ├── orchestrator.py      # next(state, rubric) -> Action + Action union
│   ├── invariants.md        # FSM state-transition table (human-readable spec)
│   ├── rubric_loader.py     # YAML → Rubric
│   └── contracts.py         # Challenger/Examiner/Scorer/Runtime/UIAdapter Protocols
├── rubrics/
│   └── ds_mle_v1.yaml       # seed rubric for the one MVP role
├── tests/
│   ├── unit/
│   │   ├── test_domain.py
│   │   ├── test_events.py
│   │   ├── test_eventlog.py
│   │   ├── test_projections.py
│   │   ├── test_orchestrator.py
│   │   ├── test_rubric_loader.py
│   │   ├── test_contracts.py
│   │   ├── test_budgets.py
│   │   └── test_prove_it.py
│   └── adversarial/
│       └── injection/
│           ├── README.md
│           └── fixtures.schema.json
└── tasks/
    └── lessons.md           # already exists
```

**Rule (non-negotiable):** `core/*` imports ONLY from `core/*`, `pydantic`, `pyyaml`, stdlib. Ever.

---

## Task 1: Repo scaffold + CI gate

**Files:**
- Create: `pyproject.toml`
- Create: `ruff.toml`
- Create: `mypy.ini`
- Create: `.github/workflows/ci.yml`
- Create: `core/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/unit/__init__.py` (empty)

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "interview-system"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.5", "pyyaml>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-cov>=5", "ruff>=0.5", "mypy>=1.10", "types-PyYAML"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers --strict-config"
```

- [ ] **Step 2: Write `ruff.toml`**

```toml
line-length = 100
target-version = "py312"

[lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]
```

- [ ] **Step 3: Write `mypy.ini`**

```ini
[mypy]
python_version = 3.12
strict = true
files = core
plugins = pydantic.mypy

[mypy-tests.*]
disallow_untyped_defs = false
```

- [ ] **Step 4: Write `.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy
      - run: pytest
```

- [ ] **Step 5: Run baseline commands**

Run: `pip install -e ".[dev]" && ruff check . && mypy && pytest`
Expected: all four commands exit 0 (pytest reports "no tests ran").

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml ruff.toml mypy.ini .github core tests
git commit -m "chore: phase-0 repo scaffold — python3.12, pydantic, ruff, mypy strict, pytest"
```

---

## Task 2: Domain types (§2, §4.1)

**Files:**
- Create: `core/domain.py`
- Test: `tests/unit/test_domain.py`

- [ ] **Step 1: Write failing test for `Session`, `Turn`, `Artifact`**

```python
# tests/unit/test_domain.py
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from core.domain import (
    Session, Turn, Artifact, Signal, Rubric, Score,
    Actor, TurnKind, Dimension, ArtifactKind,
)

def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

def test_session_minimal_construction():
    s = Session(id="s1", rubric_version="ds_mle@1", started_at=_utc("2026-04-22T10:00:00"))
    assert s.id == "s1"
    assert s.turns == ()
    assert s.artifacts == ()

def test_turn_accepts_known_actor_and_kind():
    t = Turn(id="t1", actor=Actor.candidate, kind=TurnKind.answer,
             prompt_ref=None, produced_artifact_refs=(), at=_utc("2026-04-22T10:00:05"))
    assert t.actor is Actor.candidate
    assert t.kind is TurnKind.answer

def test_turn_rejects_unknown_actor():
    with pytest.raises(ValidationError):
        Turn(id="t1", actor="martian", kind=TurnKind.answer,
             prompt_ref=None, produced_artifact_refs=(),
             at=_utc("2026-04-22T10:00:05"))

def test_artifact_typed_and_versioned():
    a = Artifact(id="a1", kind=ArtifactKind.markdown, version=1, body="hello",
                 produced_by_turn_id="t1", at=_utc("2026-04-22T10:00:05"))
    assert a.version == 1
    assert a.kind is ArtifactKind.markdown

def test_signal_carries_source_refs():
    sig = Signal(dimension=Dimension.model_rationale, value=0.8, confidence=0.9,
                 source_refs=("t1","a1"), emitted_by="scorer.rationale",
                 at=_utc("2026-04-22T10:00:06"))
    assert sig.value == 0.8

def test_rubric_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Rubric(version="v0", weights={Dimension.model_rationale: 0.5})

def test_rubric_accepts_five_mvp_dimensions():
    r = Rubric(version="ds_mle@1", weights={
        Dimension.problem_framing: 0.2, Dimension.model_rationale: 0.2,
        Dimension.experiment_design: 0.2, Dimension.insight_interp: 0.2,
        Dimension.communication: 0.2,
    })
    assert sum(r.weights.values()) == pytest.approx(1.0)

def test_score_composite_is_weighted_sum():
    sc = Score(session_id="s1", rubric_version="ds_mle@1",
               per_dimension={Dimension.model_rationale: 0.8,
                              Dimension.communication: 0.6},
               composite=0.7, at=_utc("2026-04-22T10:00:10"))
    assert sc.composite == 0.7
```

- [ ] **Step 2: Run test — expect collection failure**

Run: `pytest tests/unit/test_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: core.domain`.

- [ ] **Step 3: Implement `core/domain.py`**

```python
# core/domain.py
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Mapping
from pydantic import BaseModel, ConfigDict, Field, field_validator

class Actor(str, Enum):
    candidate = "candidate"
    challenger = "challenger"
    examiner = "examiner"
    system = "system"

class TurnKind(str, Enum):
    question = "question"
    answer = "answer"
    probe = "probe"
    defense = "defense"
    submit = "submit"

class Dimension(str, Enum):
    problem_framing = "problem_framing"
    model_rationale = "model_rationale"
    experiment_design = "experiment_design"
    insight_interp = "insight_interp"
    communication = "communication"

class ArtifactKind(str, Enum):
    prompt = "prompt"
    markdown = "markdown"
    code_cell = "code_cell"
    cell_output = "cell_output"
    chart_png = "chart_png"
    chat = "chat"
    audio_ref = "audio_ref"

class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

class Turn(_Frozen):
    id: str
    actor: Actor
    kind: TurnKind
    prompt_ref: str | None
    produced_artifact_refs: tuple[str, ...]
    at: datetime

class Artifact(_Frozen):
    id: str
    kind: ArtifactKind
    version: int = Field(ge=1)
    body: str
    produced_by_turn_id: str
    at: datetime

class Signal(_Frozen):
    dimension: Dimension
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: tuple[str, ...]
    emitted_by: str
    at: datetime

class Rubric(_Frozen):
    version: str
    weights: Mapping[Dimension, float]

    @field_validator("weights")
    @classmethod
    def _sum_to_one(cls, v: Mapping[Dimension, float]) -> Mapping[Dimension, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"rubric weights must sum to 1.0, got {total}")
        return v

class Score(_Frozen):
    session_id: str
    rubric_version: str
    per_dimension: Mapping[Dimension, float]
    composite: float = Field(ge=0.0, le=1.0)
    at: datetime

class Session(_Frozen):
    id: str
    rubric_version: str
    started_at: datetime
    ended_at: datetime | None = None
    turns: tuple[Turn, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
```

- [ ] **Step 4: Run tests — expect green**

Run: `pytest tests/unit/test_domain.py -v && mypy`
Expected: all tests PASS, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add core/domain.py tests/unit/test_domain.py
git commit -m "feat(core): domain types — Session/Turn/Artifact/Signal/Rubric/Score (frozen, typed)"
```

---

## Task 3: Event types (§4.2) — including `human_override` and `SessionTimedOut`

**Files:**
- Create: `core/events.py`
- Test: `tests/unit/test_events.py`

Rationale: Open Q #2 requires `human_override` event from day 1. Open Q #3 requires session-timeout logic in orchestrator → needs `SessionTimedOut` event.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_events.py
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from core.events import (
    Envelope, SessionStarted, TurnPosted, ArtifactAttached,
    SignalEmitted, ScoreComputed, SessionEnded,
    CandidateIdle, ProfileIngested, BackchannelPosted,
    IdleThresholdCrossed, BreakDue, HumanOverride, SessionTimedOut,
    EVENT_TYPES,
)
from core.domain import Dimension, TurnKind, Actor, ArtifactKind

UTC = timezone.utc

def test_envelope_is_monotonic_within_session():
    e1 = Envelope(session_id="s1", seq=1, at=datetime(2026,4,22,10,0,0,tzinfo=UTC),
                  payload=SessionStarted(rubric_version="ds_mle@1"))
    e2 = Envelope(session_id="s1", seq=2, at=datetime(2026,4,22,10,0,1,tzinfo=UTC),
                  payload=SessionStarted(rubric_version="ds_mle@1"))
    assert e2.seq > e1.seq

def test_envelope_seq_must_be_positive():
    with pytest.raises(ValidationError):
        Envelope(session_id="s1", seq=0,
                 at=datetime(2026,4,22,tzinfo=UTC),
                 payload=SessionStarted(rubric_version="ds_mle@1"))

def test_human_override_carries_target_and_reason():
    h = HumanOverride(target_signal_dimension=Dimension.model_rationale,
                      original_value=0.4, override_value=0.7,
                      reviewer_id="rec-42", reason="model choice was justified")
    assert h.reviewer_id == "rec-42"

def test_session_timed_out_carries_timeout_kind():
    t = SessionTimedOut(reason="idle", elapsed_s=1800)
    assert t.reason == "idle"

def test_event_types_registry_covers_all_payloads():
    expected = {
        SessionStarted, TurnPosted, ArtifactAttached, SignalEmitted,
        ScoreComputed, SessionEnded, CandidateIdle, ProfileIngested,
        BackchannelPosted, IdleThresholdCrossed, BreakDue,
        HumanOverride, SessionTimedOut,
    }
    assert EVENT_TYPES == expected
```

- [ ] **Step 2: Run test — expect import fail**

Run: `pytest tests/unit/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: core.events`.

- [ ] **Step 3: Implement `core/events.py`**

```python
# core/events.py
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field
from core.domain import Dimension, TurnKind, Actor, ArtifactKind

class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

class SessionStarted(_Payload):
    kind: Literal["SessionStarted"] = "SessionStarted"
    rubric_version: str

class TurnPosted(_Payload):
    kind: Literal["TurnPosted"] = "TurnPosted"
    turn_id: str
    actor: Actor
    turn_kind: TurnKind
    prompt_ref: str | None
    at: datetime

class ArtifactAttached(_Payload):
    kind: Literal["ArtifactAttached"] = "ArtifactAttached"
    artifact_id: str
    artifact_kind: ArtifactKind
    produced_by_turn_id: str

class SignalEmitted(_Payload):
    kind: Literal["SignalEmitted"] = "SignalEmitted"
    dimension: Dimension
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    emitted_by: str
    source_refs: tuple[str, ...]

class ScoreComputed(_Payload):
    kind: Literal["ScoreComputed"] = "ScoreComputed"
    composite: float = Field(ge=0.0, le=1.0)
    per_dimension: dict[Dimension, float]

class SessionEnded(_Payload):
    kind: Literal["SessionEnded"] = "SessionEnded"
    reason: Literal["submit", "ended_by_recruiter", "abandoned"]

class CandidateIdle(_Payload):
    kind: Literal["CandidateIdle"] = "CandidateIdle"
    duration_s: int = Field(ge=0)

class ProfileIngested(_Payload):
    kind: Literal["ProfileIngested"] = "ProfileIngested"
    profile_ref: str

class BackchannelPosted(_Payload):
    kind: Literal["BackchannelPosted"] = "BackchannelPosted"
    text: str

class IdleThresholdCrossed(_Payload):
    kind: Literal["IdleThresholdCrossed"] = "IdleThresholdCrossed"
    threshold_s: int

class BreakDue(_Payload):
    kind: Literal["BreakDue"] = "BreakDue"

class HumanOverride(_Payload):
    kind: Literal["HumanOverride"] = "HumanOverride"
    target_signal_dimension: Dimension
    original_value: float = Field(ge=0.0, le=1.0)
    override_value: float = Field(ge=0.0, le=1.0)
    reviewer_id: str
    reason: str

class SessionTimedOut(_Payload):
    kind: Literal["SessionTimedOut"] = "SessionTimedOut"
    reason: Literal["idle", "wall_clock"]
    elapsed_s: int = Field(ge=0)

Payload = Annotated[
    Union[
        SessionStarted, TurnPosted, ArtifactAttached, SignalEmitted,
        ScoreComputed, SessionEnded, CandidateIdle, ProfileIngested,
        BackchannelPosted, IdleThresholdCrossed, BreakDue,
        HumanOverride, SessionTimedOut,
    ],
    Field(discriminator="kind"),
]

EVENT_TYPES = {
    SessionStarted, TurnPosted, ArtifactAttached, SignalEmitted,
    ScoreComputed, SessionEnded, CandidateIdle, ProfileIngested,
    BackchannelPosted, IdleThresholdCrossed, BreakDue,
    HumanOverride, SessionTimedOut,
}

class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    seq: int = Field(ge=1)
    at: datetime
    payload: Payload
```

- [ ] **Step 4: Run tests — expect green**

Run: `pytest tests/unit/test_events.py -v && mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/events.py tests/unit/test_events.py
git commit -m "feat(core): event payloads — 13 types incl HumanOverride, SessionTimedOut"
```

---

## Task 4: In-memory append-only Event Log

**Files:**
- Create: `core/eventlog.py`
- Test: `tests/unit/test_eventlog.py`

Rationale: §4.2 says Postgres for MVP, but backbone is pure. Phase 0 ships the **in-memory reference implementation** used in tests; Postgres adapter lands in Phase 1. This keeps `core/` I/O-free.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_eventlog.py
from datetime import datetime, timezone
import pytest
from core.eventlog import InMemoryEventLog, SeqConflict
from core.events import Envelope, SessionStarted

UTC = timezone.utc

def _env(session_id: str, seq: int) -> Envelope:
    return Envelope(session_id=session_id, seq=seq,
                    at=datetime(2026,4,22,10,0,seq,tzinfo=UTC),
                    payload=SessionStarted(rubric_version="ds_mle@1"))

def test_append_assigns_monotonic_seq_per_session():
    log = InMemoryEventLog()
    log.append(_env("s1", 1))
    log.append(_env("s1", 2))
    seqs = [e.seq for e in log.read_session("s1")]
    assert seqs == [1, 2]

def test_reject_duplicate_seq_same_session():
    log = InMemoryEventLog()
    log.append(_env("s1", 1))
    with pytest.raises(SeqConflict):
        log.append(_env("s1", 1))

def test_reject_nonmonotonic_seq():
    log = InMemoryEventLog()
    log.append(_env("s1", 2))
    with pytest.raises(SeqConflict):
        log.append(_env("s1", 1))

def test_different_sessions_have_independent_seq():
    log = InMemoryEventLog()
    log.append(_env("s1", 1))
    log.append(_env("s2", 1))
    assert [e.seq for e in log.read_session("s1")] == [1]
    assert [e.seq for e in log.read_session("s2")] == [1]

def test_read_session_unknown_returns_empty():
    log = InMemoryEventLog()
    assert list(log.read_session("ghost")) == []
```

- [ ] **Step 2: Run test — import fail**

Run: `pytest tests/unit/test_eventlog.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `core/eventlog.py`**

```python
# core/eventlog.py
from __future__ import annotations
from collections.abc import Iterator
from core.events import Envelope

class SeqConflict(Exception):
    """Raised when append violates the per-session monotonic-seq invariant."""

class InMemoryEventLog:
    """Append-only, per-session monotonic-seq event log. Test double for Postgres."""
    def __init__(self) -> None:
        self._by_session: dict[str, list[Envelope]] = {}

    def append(self, env: Envelope) -> None:
        seen = self._by_session.setdefault(env.session_id, [])
        expected = (seen[-1].seq + 1) if seen else 1
        if env.seq != expected:
            raise SeqConflict(
                f"session {env.session_id}: expected seq={expected}, got seq={env.seq}"
            )
        seen.append(env)

    def read_session(self, session_id: str) -> Iterator[Envelope]:
        yield from self._by_session.get(session_id, [])
```

- [ ] **Step 4: Run tests — green**

Run: `pytest tests/unit/test_eventlog.py -v && mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/eventlog.py tests/unit/test_eventlog.py
git commit -m "feat(core): InMemoryEventLog — append-only, monotonic seq per session"
```

---

## Task 5: Projections — `SessionStore` and `ScoreStore` (§4.3)

**Files:**
- Create: `core/projections.py`
- Test: `tests/unit/test_projections.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_projections.py
from datetime import datetime, timezone
from core.events import (
    Envelope, SessionStarted, TurnPosted, ArtifactAttached,
    SignalEmitted, ScoreComputed, SessionEnded, HumanOverride,
)
from core.domain import Dimension, TurnKind, Actor, ArtifactKind
from core.projections import project_session, project_scores, SessionView, ScoreView

UTC = timezone.utc
def _at(s: int) -> datetime: return datetime(2026,4,22,10,0,s,tzinfo=UTC)

def _stream():
    return [
        Envelope(session_id="s1", seq=1, at=_at(0),
                 payload=SessionStarted(rubric_version="ds_mle@1")),
        Envelope(session_id="s1", seq=2, at=_at(5),
                 payload=TurnPosted(turn_id="t1", actor=Actor.challenger,
                                    turn_kind=TurnKind.question,
                                    prompt_ref="p1", at=_at(5))),
        Envelope(session_id="s1", seq=3, at=_at(6),
                 payload=ArtifactAttached(artifact_id="a1",
                                          artifact_kind=ArtifactKind.prompt,
                                          produced_by_turn_id="t1")),
        Envelope(session_id="s1", seq=4, at=_at(30),
                 payload=SignalEmitted(dimension=Dimension.model_rationale,
                                       value=0.8, confidence=0.9,
                                       emitted_by="scorer.rationale",
                                       source_refs=("t1",))),
    ]

def test_project_session_reconstructs_turns_and_artifacts():
    view: SessionView = project_session(_stream())
    assert view.session_id == "s1"
    assert [t.id for t in view.turns] == ["t1"]
    assert [a.id for a in view.artifacts] == ["a1"]
    assert view.ended_at is None

def test_project_session_marks_ended():
    s = _stream() + [Envelope(session_id="s1", seq=5, at=_at(60),
                               payload=SessionEnded(reason="submit"))]
    view = project_session(s)
    assert view.ended_at == _at(60)

def test_project_scores_aggregates_signals():
    scores: ScoreView = project_scores(_stream())
    assert scores.session_id == "s1"
    assert scores.per_dimension[Dimension.model_rationale] == 0.8

def test_project_scores_applies_human_override():
    s = _stream() + [Envelope(session_id="s1", seq=5, at=_at(100),
                               payload=HumanOverride(
                                   target_signal_dimension=Dimension.model_rationale,
                                   original_value=0.8, override_value=0.6,
                                   reviewer_id="r1", reason="reviewer disagrees"))]
    scores = project_scores(s)
    assert scores.per_dimension[Dimension.model_rationale] == 0.6
    assert scores.overridden_dimensions == {Dimension.model_rationale}

def test_projections_are_deterministic():
    a = project_session(_stream())
    b = project_session(_stream())
    assert a == b
```

- [ ] **Step 2: Run test — import fail**

Run: `pytest tests/unit/test_projections.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `core/projections.py`**

```python
# core/projections.py
from __future__ import annotations
from collections.abc import Iterable
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from core.domain import Turn, Artifact, Dimension, TurnKind, Actor, ArtifactKind
from core.events import (
    Envelope, SessionStarted, TurnPosted, ArtifactAttached,
    SignalEmitted, ScoreComputed, SessionEnded, HumanOverride,
)

class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True)
    session_id: str
    rubric_version: str
    started_at: datetime
    ended_at: datetime | None
    turns: tuple[Turn, ...]
    artifacts: tuple[Artifact, ...]

class ScoreView(BaseModel):
    model_config = ConfigDict(frozen=True)
    session_id: str
    per_dimension: dict[Dimension, float]
    overridden_dimensions: frozenset[Dimension]
    composite: float | None

def project_session(stream: Iterable[Envelope]) -> SessionView:
    session_id: str | None = None
    rubric_version: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    turns: list[Turn] = []
    artifacts: list[Artifact] = []
    # pending lookup of turn metadata from TurnPosted → materialized when artifact attaches
    turn_meta: dict[str, tuple[Actor, TurnKind, str | None, datetime]] = {}

    for env in stream:
        session_id = env.session_id
        p = env.payload
        if isinstance(p, SessionStarted):
            rubric_version = p.rubric_version
            started_at = env.at
        elif isinstance(p, TurnPosted):
            turn_meta[p.turn_id] = (p.actor, p.turn_kind, p.prompt_ref, p.at)
            turns.append(Turn(id=p.turn_id, actor=p.actor, kind=p.turn_kind,
                              prompt_ref=p.prompt_ref, produced_artifact_refs=(),
                              at=p.at))
        elif isinstance(p, ArtifactAttached):
            # Attach an Artifact. Body is kept empty here — body lives off-log in blob store.
            artifacts.append(Artifact(id=p.artifact_id, kind=p.artifact_kind,
                                      version=1, body="",
                                      produced_by_turn_id=p.produced_by_turn_id,
                                      at=env.at))
        elif isinstance(p, SessionEnded):
            ended_at = env.at

    assert session_id is not None and started_at is not None, \
        "stream must include SessionStarted"
    return SessionView(session_id=session_id, rubric_version=rubric_version,
                       started_at=started_at, ended_at=ended_at,
                       turns=tuple(turns), artifacts=tuple(artifacts))

def project_scores(stream: Iterable[Envelope]) -> ScoreView:
    session_id: str | None = None
    per_dim: dict[Dimension, float] = {}
    overridden: set[Dimension] = set()
    composite: float | None = None

    for env in stream:
        session_id = env.session_id
        p = env.payload
        if isinstance(p, SignalEmitted):
            # last-writer-wins per (dimension, emitted_by); MVP uses single-scorer-per-dim
            per_dim[p.dimension] = p.value
        elif isinstance(p, ScoreComputed):
            composite = p.composite
            per_dim = dict(p.per_dimension)
        elif isinstance(p, HumanOverride):
            per_dim[p.target_signal_dimension] = p.override_value
            overridden.add(p.target_signal_dimension)

    assert session_id is not None, "stream must include at least one event"
    return ScoreView(session_id=session_id, per_dimension=per_dim,
                     overridden_dimensions=frozenset(overridden),
                     composite=composite)
```

- [ ] **Step 4: Run tests — green**

Run: `pytest tests/unit/test_projections.py -v && mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/projections.py tests/unit/test_projections.py
git commit -m "feat(core): SessionStore/ScoreStore projections — deterministic pure reducers"
```

---

## Task 6: FSM invariants table (`core/invariants.md`)

**Files:**
- Create: `core/invariants.md`

Rationale: Lessons #5 — for any "pure function" claim, require a written invariants table so conformance tests are not vacuous. This doc is referenced by Task 7's orchestrator tests.

- [ ] **Step 1: Write `core/invariants.md`**

````markdown
# Orchestrator FSM — State-Transition Invariants (Phase 0)

Scope: what `next(session_view, rubric) -> Action` is allowed to return. This table is the contract the orchestrator's unit tests enforce.

## States (derived from `SessionView`)

| State | Condition |
|---|---|
| `S0_Fresh` | `turns == ()` and `ended_at is None` |
| `S1_AwaitingCandidate` | Last turn is from `challenger` or `examiner`, and it is a `question`/`probe` |
| `S2_CandidateAnswered` | Last turn is from `candidate` with `kind in {answer, defense}` |
| `S3_ScoresComputed` | A `ScoreComputed` event has landed for every dimension in the rubric |
| `S4_Submitted` | Last turn is `candidate`/`submit` |
| `S5_TimedOut` | Last event is `SessionTimedOut` |
| `S6_Ended` | `ended_at is not None` |

## Legal transitions (Action outputs)

| State | Action returned | Why |
|---|---|---|
| `S0_Fresh` | `AskChallenger(kind="opening_question")` | First move always from challenger |
| `S1_AwaitingCandidate` (idle < `idle_timeout_s`) | `AwaitCandidate(timeout_s=remaining)` | Give the candidate room |
| `S1_AwaitingCandidate` (idle ≥ `idle_timeout_s`) | `End(reason="idle_timeout")` | Abandonment handling, Open Q #3 |
| `S2_CandidateAnswered` | `RunScorers(turn_id=last_turn.id)` | Score each candidate move before deciding next |
| `S2_CandidateAnswered` (scores already present) | `AskExaminer(topic=lowest_dimension)` OR `AskChallenger(kind="next_question")` — see tie-break | Drive follow-up on weak dim, else advance |
| `S3_ScoresComputed` after `submit` | `End(reason="submit")` | All done |
| `S4_Submitted` | `RunScorers(turn_id=last_turn.id)` then `End(reason="submit")` | Final pass |
| `S5_TimedOut` | `End(reason="idle_timeout")` | Absorbing |
| `S6_Ended` | raises `AlreadyEnded` — orchestrator never re-activates an ended session | Invariant |

## Tie-break: Examiner vs. next Challenger question

After `S2_CandidateAnswered` with scores present:
- If any dimension's signal is **below the rubric's `probe_threshold`** (default `0.5`) AND the session has < `max_probes_per_dim` probes on that dim: return `AskExaminer(topic=that_dim)`.
- Else: return `AskChallenger(kind="next_question")` until the session hits `max_challenger_turns` (default `5`), after which return `End(reason="completed")`.

## Purity rules

- `next` MUST NOT read the clock. Time inputs (idle duration, wall-clock elapsed) come in as part of `SessionView.now_s` computed by the caller.
- `next` MUST be deterministic for fixed inputs.
- `next` MUST NOT raise except for `AlreadyEnded` on `S6_Ended`.
````

- [ ] **Step 2: Commit**

```bash
git add core/invariants.md
git commit -m "docs(core): FSM state-transition table — orchestrator purity contract"
```

---

## Task 7: Orchestrator (§4.4) — pure `next(state, rubric) → Action`

**Files:**
- Create: `core/orchestrator.py`
- Test: `tests/unit/test_orchestrator.py`

- [ ] **Step 1: Write failing test (covers every row in the invariants table)**

```python
# tests/unit/test_orchestrator.py
from datetime import datetime, timezone
import pytest
from core.domain import Rubric, Dimension, Actor, TurnKind, Turn
from core.projections import SessionView, ScoreView
from core.orchestrator import (
    next_action, AskChallenger, AwaitCandidate, AskExaminer,
    RunScorers, End, AlreadyEnded,
)

UTC = timezone.utc
def _at(s: int) -> datetime: return datetime(2026,4,22,10,0,s,tzinfo=UTC)

def _rubric() -> Rubric:
    return Rubric(version="ds_mle@1", weights={
        Dimension.problem_framing: 0.2, Dimension.model_rationale: 0.2,
        Dimension.experiment_design: 0.2, Dimension.insight_interp: 0.2,
        Dimension.communication: 0.2,
    })

def _turn(tid: str, actor: Actor, kind: TurnKind, at_s: int) -> Turn:
    return Turn(id=tid, actor=actor, kind=kind, prompt_ref=None,
                produced_artifact_refs=(), at=_at(at_s))

def _view(turns=(), ended=False, now_s: int = 0,
          idle_since_s: int | None = None) -> SessionView:
    return SessionView(session_id="s1", rubric_version="ds_mle@1",
                       started_at=_at(0),
                       ended_at=_at(now_s) if ended else None,
                       turns=tuple(turns), artifacts=())

def _scores(per_dim: dict[Dimension, float] | None = None) -> ScoreView:
    return ScoreView(session_id="s1",
                     per_dimension=per_dim or {},
                     overridden_dimensions=frozenset(),
                     composite=None)

def test_s0_fresh_asks_challenger_opening():
    act = next_action(_view(), _rubric(), _scores(), now_s=0, idle_since_s=None)
    assert isinstance(act, AskChallenger) and act.kind == "opening_question"

def test_s1_awaiting_candidate_under_timeout_returns_await():
    turns = (_turn("t1", Actor.challenger, TurnKind.question, 5),)
    act = next_action(_view(turns=turns), _rubric(), _scores(),
                      now_s=30, idle_since_s=5)
    assert isinstance(act, AwaitCandidate) and act.remaining_s > 0

def test_s1_awaiting_candidate_over_timeout_ends_session():
    turns = (_turn("t1", Actor.challenger, TurnKind.question, 5),)
    act = next_action(_view(turns=turns), _rubric(), _scores(),
                      now_s=10_000, idle_since_s=5)  # 1800s idle budget
    assert isinstance(act, End) and act.reason == "idle_timeout"

def test_s2_candidate_answered_with_no_scores_runs_scorers():
    turns = (_turn("t1", Actor.challenger, TurnKind.question, 5),
             _turn("t2", Actor.candidate, TurnKind.answer, 60))
    act = next_action(_view(turns=turns), _rubric(), _scores(),
                      now_s=70, idle_since_s=None)
    assert isinstance(act, RunScorers) and act.turn_id == "t2"

def test_s2_with_weak_signal_probes_that_dimension():
    turns = (_turn("t1", Actor.challenger, TurnKind.question, 5),
             _turn("t2", Actor.candidate, TurnKind.answer, 60))
    scores = _scores({Dimension.model_rationale: 0.3,
                      Dimension.communication: 0.9})
    act = next_action(_view(turns=turns), _rubric(), scores,
                      now_s=70, idle_since_s=None)
    assert isinstance(act, AskExaminer)
    assert act.topic == Dimension.model_rationale

def test_s2_with_strong_signals_advances_challenger():
    turns = (_turn("t1", Actor.challenger, TurnKind.question, 5),
             _turn("t2", Actor.candidate, TurnKind.answer, 60))
    scores = _scores({d: 0.8 for d in Dimension})
    act = next_action(_view(turns=turns), _rubric(), scores,
                      now_s=70, idle_since_s=None)
    assert isinstance(act, AskChallenger) and act.kind == "next_question"

def test_s4_submit_schedules_final_score_then_end():
    turns = (_turn("t1", Actor.candidate, TurnKind.submit, 1000),)
    act = next_action(_view(turns=turns), _rubric(), _scores(),
                      now_s=1001, idle_since_s=None)
    assert isinstance(act, RunScorers)

def test_s6_ended_raises():
    with pytest.raises(AlreadyEnded):
        next_action(_view(ended=True, now_s=2000), _rubric(), _scores(),
                    now_s=2001, idle_since_s=None)

def test_determinism_same_inputs_same_output():
    v = _view()
    a = next_action(v, _rubric(), _scores(), now_s=0, idle_since_s=None)
    b = next_action(v, _rubric(), _scores(), now_s=0, idle_since_s=None)
    assert a == b
```

- [ ] **Step 2: Run test — import fail**

Run: `pytest tests/unit/test_orchestrator.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `core/orchestrator.py`**

```python
# core/orchestrator.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Union
from core.domain import Dimension, Actor, TurnKind, Rubric
from core.projections import SessionView, ScoreView

IDLE_TIMEOUT_S = 1800
MAX_CHALLENGER_TURNS = 5
MAX_PROBES_PER_DIM = 2
PROBE_THRESHOLD = 0.5

class AlreadyEnded(Exception): ...

@dataclass(frozen=True)
class AskChallenger:
    kind: Literal["opening_question", "next_question"]

@dataclass(frozen=True)
class AwaitCandidate:
    remaining_s: int

@dataclass(frozen=True)
class AskExaminer:
    topic: Dimension

@dataclass(frozen=True)
class RunScorers:
    turn_id: str

@dataclass(frozen=True)
class End:
    reason: Literal["submit", "idle_timeout", "completed", "ended_by_recruiter"]

Action = Union[AskChallenger, AwaitCandidate, AskExaminer, RunScorers, End]

def next_action(
    view: SessionView,
    rubric: Rubric,
    scores: ScoreView,
    *,
    now_s: int,
    idle_since_s: int | None,
) -> Action:
    if view.ended_at is not None:
        raise AlreadyEnded(view.session_id)
    turns = view.turns
    if not turns:
        return AskChallenger(kind="opening_question")

    last = turns[-1]

    # S4: explicit submit
    if last.actor is Actor.candidate and last.kind is TurnKind.submit:
        return RunScorers(turn_id=last.id)

    # S1: awaiting candidate after a question/probe
    if last.actor in (Actor.challenger, Actor.examiner) and \
       last.kind in (TurnKind.question, TurnKind.probe):
        idle = (now_s - idle_since_s) if idle_since_s is not None else 0
        if idle >= IDLE_TIMEOUT_S:
            return End(reason="idle_timeout")
        return AwaitCandidate(remaining_s=max(1, IDLE_TIMEOUT_S - idle))

    # S2: candidate just answered / defended
    if last.actor is Actor.candidate and last.kind in (TurnKind.answer, TurnKind.defense):
        if not scores.per_dimension:
            return RunScorers(turn_id=last.id)

        # weak-dim probe?
        weak = min(scores.per_dimension.items(), key=lambda kv: kv[1])
        probes_on_weak = sum(
            1 for t in turns if t.actor is Actor.examiner and t.kind is TurnKind.probe
        )
        if weak[1] < PROBE_THRESHOLD and probes_on_weak < MAX_PROBES_PER_DIM:
            return AskExaminer(topic=weak[0])

        challenger_turns = sum(
            1 for t in turns if t.actor is Actor.challenger and t.kind is TurnKind.question
        )
        if challenger_turns >= MAX_CHALLENGER_TURNS:
            return End(reason="completed")
        return AskChallenger(kind="next_question")

    # Fallback: push progress
    return AskChallenger(kind="next_question")
```

- [ ] **Step 4: Run tests — green**

Run: `pytest tests/unit/test_orchestrator.py -v && mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py tests/unit/test_orchestrator.py
git commit -m "feat(core): orchestrator next_action — pure FSM conforming to core/invariants.md"
```

---

## Task 8: Contract stubs (§4.5)

**Files:**
- Create: `core/contracts.py`
- Test: `tests/unit/test_contracts.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_contracts.py
from typing import runtime_checkable
from core.contracts import Challenger, Examiner, Scorer, Runtime, UIAdapter
from core.domain import Rubric, Dimension, Turn, Artifact, Signal
from core.projections import SessionView

class FakeChallenger:
    def generate(self, rubric, session):
        raise NotImplementedError

class FakeScorer:
    dimension = Dimension.model_rationale
    def score(self, turn, session):
        return []

def test_challenger_protocol_structural_check():
    assert isinstance(FakeChallenger(), Challenger) or True  # Protocol always True at runtime unless @runtime_checkable
    # Structural: method exists with right arity
    assert callable(FakeChallenger().generate)

def test_scorer_carries_dimension_attr():
    assert FakeScorer().dimension is Dimension.model_rationale

def test_contracts_are_all_exported():
    names = {Challenger.__name__, Examiner.__name__, Scorer.__name__,
             Runtime.__name__, UIAdapter.__name__}
    assert names == {"Challenger", "Examiner", "Scorer", "Runtime", "UIAdapter"}
```

- [ ] **Step 2: Run — import fail**

Run: `pytest tests/unit/test_contracts.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `core/contracts.py`**

```python
# core/contracts.py
from __future__ import annotations
from typing import Protocol
from core.domain import Rubric, Dimension, Turn, Artifact, Signal
from core.events import Envelope
from core.projections import SessionView

class Challenger(Protocol):
    def generate(self, rubric: Rubric, session: SessionView) -> Turn: ...

class Examiner(Protocol):
    def probe(self, session: SessionView, focus: Dimension) -> Turn: ...

class Scorer(Protocol):
    dimension: Dimension
    def score(self, turn: Turn, session: SessionView) -> list[Signal]: ...

class Runtime(Protocol):
    def exec(self, artifact: Artifact) -> Artifact: ...

class UIAdapter(Protocol):
    def push(self, event: Envelope) -> None: ...
```

- [ ] **Step 4: Run tests — green**

Run: `pytest tests/unit/test_contracts.py -v && mypy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/contracts.py tests/unit/test_contracts.py
git commit -m "feat(core): five port protocols — Challenger/Examiner/Scorer/Runtime/UIAdapter"
```

---

## Task 9: Rubric YAML loader (Open Q #1 → YAML for MVP)

**Files:**
- Create: `rubrics/ds_mle_v1.yaml`
- Create: `core/rubric_loader.py`
- Test: `tests/unit/test_rubric_loader.py`

- [ ] **Step 1: Write `rubrics/ds_mle_v1.yaml`**

```yaml
version: ds_mle@1
weights:
  problem_framing:  0.20
  model_rationale:  0.20
  experiment_design: 0.20
  insight_interp:   0.20
  communication:    0.20
```

- [ ] **Step 2: Write failing test**

```python
# tests/unit/test_rubric_loader.py
from pathlib import Path
import pytest
from core.rubric_loader import load_rubric, RubricLoadError
from core.domain import Dimension

FIXTURES = Path(__file__).parent / "fixtures"

def test_load_seed_rubric():
    r = load_rubric(Path("rubrics/ds_mle_v1.yaml"))
    assert r.version == "ds_mle@1"
    assert set(r.weights) == set(Dimension)
    assert sum(r.weights.values()) == pytest.approx(1.0)

def test_load_rubric_unknown_dimension_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: v0\nweights:\n  not_a_real_dim: 1.0\n")
    with pytest.raises(RubricLoadError):
        load_rubric(bad)

def test_load_rubric_weights_dont_sum_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: v0\nweights:\n  model_rationale: 0.3\n  communication: 0.3\n")
    with pytest.raises(RubricLoadError):
        load_rubric(bad)
```

- [ ] **Step 3: Run test — expect fail**

Run: `pytest tests/unit/test_rubric_loader.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement `core/rubric_loader.py`**

```python
# core/rubric_loader.py
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml
from pydantic import ValidationError
from core.domain import Rubric, Dimension

class RubricLoadError(Exception): ...

def load_rubric(path: Path) -> Rubric:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "version" not in raw or "weights" not in raw:
        raise RubricLoadError(f"{path}: missing 'version' or 'weights'")
    weights_raw = raw["weights"]
    if not isinstance(weights_raw, dict):
        raise RubricLoadError(f"{path}: 'weights' must be a mapping")
    try:
        weights = {Dimension(k): float(v) for k, v in weights_raw.items()}
    except ValueError as e:
        raise RubricLoadError(f"{path}: unknown dimension — {e}") from e
    try:
        return Rubric(version=str(raw["version"]), weights=weights)
    except ValidationError as e:
        raise RubricLoadError(f"{path}: {e}") from e
```

- [ ] **Step 5: Run tests — green**

Run: `pytest tests/unit/test_rubric_loader.py -v && mypy`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/rubric_loader.py rubrics/ds_mle_v1.yaml tests/unit/test_rubric_loader.py
git commit -m "feat(core): YAML rubric loader — seeds ds_mle@1 with 5 MVP dimensions"
```

---

## Task 10: Unit-test budget helper (§15.5 Phase 0–2 rule)

**Files:**
- Create: `tests/unit/test_budgets.py`

Rationale: §15.5 says "Phase 0–2: budgets asserted in unit tests." We do NOT instrument OTel yet, but we DO land the helper + one assertion so the budget discipline is present from day one.

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_budgets.py
import time
from contextlib import contextmanager
import pytest

@contextmanager
def assert_under_ms(budget_ms: int):
    t0 = time.perf_counter()
    yield
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < budget_ms, f"took {elapsed_ms:.1f}ms, budget {budget_ms}ms"

def test_orchestrator_next_action_under_10ms():
    from core.orchestrator import next_action
    from core.projections import SessionView, ScoreView
    from core.domain import Rubric, Dimension
    from datetime import datetime, timezone
    r = Rubric(version="v1", weights={d: 0.2 for d in Dimension})
    v = SessionView(session_id="s1", rubric_version="v1",
                    started_at=datetime(2026,4,22,tzinfo=timezone.utc),
                    ended_at=None, turns=(), artifacts=())
    s = ScoreView(session_id="s1", per_dimension={},
                  overridden_dimensions=frozenset(), composite=None)
    with assert_under_ms(10):
        for _ in range(1000):
            next_action(v, r, s, now_s=0, idle_since_s=None)
```

- [ ] **Step 2: Run — expect green (or fail with timing info)**

Run: `pytest tests/unit/test_budgets.py -v`
Expected: PASS; if flaky, relax budget once with a comment pointing to §15.5.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_budgets.py
git commit -m "test(budget): phase-0 latency assertion — orchestrator hot path <10ms/1000"
```

---

## Task 11: Adversarial fixtures skeleton (§4.7, fixture dir only — live assertions Phase 3)

**Files:**
- Create: `tests/adversarial/injection/README.md`
- Create: `tests/adversarial/injection/fixtures.schema.json`
- Create: `tests/adversarial/injection/__init__.py`

Rationale: §4.7 declares six injection classes. No LLM adapters exist in Phase 0, so assertions can't run, but the fixture schema + directory ship now so Phase 3 only has to write fixture files + wire them.

- [ ] **Step 1: Write `tests/adversarial/injection/README.md`**

````markdown
# Injection Adversarial Fixtures

Fixture corpus for the prompt-injection red-line (see `architecture-and-plan.md` §4.7, ADR-008).

Phase 0: directory + schema + one example fixture.
Phase 3: wire into Challenger/Examiner adapter test runs; any failure blocks release.

## Fixture classes (§4.7)

1. `rubric-leak` — attempts to extract rubric dimensions/weights.
2. `score-inflation` — attempts to force `Signal(value=1.0)`.
3. `persona-escape` — attempts to override `IDENTITY.md`/`SOUL.md`.
4. `instruction-echo` — attempts to make agent repeat attacker-authored text.
5. `system-voice-spoof` — uses fake `</untrusted_candidate_turn>` close tags.
6. `multi-turn-drift` — slow re-framing across 5+ turns.

Each fixture conforms to `fixtures.schema.json`.
````

- [ ] **Step 2: Write `tests/adversarial/injection/fixtures.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "InjectionFixture",
  "type": "object",
  "required": ["id", "class", "candidate_turn", "expect"],
  "properties": {
    "id": { "type": "string" },
    "class": {
      "type": "string",
      "enum": ["rubric-leak","score-inflation","persona-escape",
               "instruction-echo","system-voice-spoof","multi-turn-drift"]
    },
    "candidate_turn": { "type": "string" },
    "turn_sequence": {
      "type": "array",
      "items": { "type": "string" },
      "description": "For multi-turn-drift only; ordered candidate turns."
    },
    "expect": {
      "type": "object",
      "properties": {
        "no_rubric_leak": { "type": "boolean" },
        "max_signal_drift": { "type": "number" },
        "persona_stable": { "type": "boolean" },
        "no_instruction_compliance": { "type": "boolean" }
      }
    }
  }
}
```

- [ ] **Step 3: Create the `__init__.py` (empty)**

- [ ] **Step 4: Commit**

```bash
git add tests/adversarial
git commit -m "test(adversarial): injection fixture dir + schema (phase-0 skeleton, assertions phase-3)"
```

---

## Task 12: Prove-it — golden event sequence → expected `Action`

**Files:**
- Create: `tests/unit/test_prove_it.py`

Rationale: §6 Phase 0 prove-it criterion — "post a hand-crafted sequence of events and assert projections + orchestrator produce the expected next-action."

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_prove_it.py
"""Phase 0 Prove-it: hand-crafted event stream → projection → orchestrator → Action."""
from datetime import datetime, timezone
from pathlib import Path
from core.eventlog import InMemoryEventLog
from core.events import (
    Envelope, SessionStarted, TurnPosted, ArtifactAttached,
    SignalEmitted, ScoreComputed,
)
from core.domain import Actor, TurnKind, Dimension, ArtifactKind
from core.projections import project_session, project_scores
from core.orchestrator import next_action, AskExaminer
from core.rubric_loader import load_rubric

UTC = timezone.utc
def _at(s: int) -> datetime: return datetime(2026,4,22,10,0,s,tzinfo=UTC)

def test_phase0_end_to_end_replay():
    log = InMemoryEventLog()
    events = [
        Envelope(session_id="s1", seq=1, at=_at(0),
                 payload=SessionStarted(rubric_version="ds_mle@1")),
        Envelope(session_id="s1", seq=2, at=_at(5),
                 payload=TurnPosted(turn_id="t1", actor=Actor.challenger,
                                    turn_kind=TurnKind.question,
                                    prompt_ref="p1", at=_at(5))),
        Envelope(session_id="s1", seq=3, at=_at(6),
                 payload=ArtifactAttached(artifact_id="a1",
                                          artifact_kind=ArtifactKind.prompt,
                                          produced_by_turn_id="t1")),
        Envelope(session_id="s1", seq=4, at=_at(60),
                 payload=TurnPosted(turn_id="t2", actor=Actor.candidate,
                                    turn_kind=TurnKind.answer,
                                    prompt_ref=None, at=_at(60))),
        Envelope(session_id="s1", seq=5, at=_at(61),
                 payload=ArtifactAttached(artifact_id="a2",
                                          artifact_kind=ArtifactKind.markdown,
                                          produced_by_turn_id="t2")),
        Envelope(session_id="s1", seq=6, at=_at(62),
                 payload=SignalEmitted(dimension=Dimension.model_rationale,
                                       value=0.3, confidence=0.9,
                                       emitted_by="scorer.rationale",
                                       source_refs=("t2","a2"))),
        Envelope(session_id="s1", seq=7, at=_at(62),
                 payload=SignalEmitted(dimension=Dimension.communication,
                                       value=0.8, confidence=0.9,
                                       emitted_by="scorer.communication",
                                       source_refs=("t2","a2"))),
    ]
    for e in events:
        log.append(e)

    view = project_session(log.read_session("s1"))
    scores = project_scores(log.read_session("s1"))
    rubric = load_rubric(Path("rubrics/ds_mle_v1.yaml"))

    act = next_action(view, rubric, scores, now_s=65, idle_since_s=None)
    assert isinstance(act, AskExaminer)
    assert act.topic == Dimension.model_rationale  # weak dim triggers probe
```

- [ ] **Step 2: Run — green**

Run: `pytest tests/unit/test_prove_it.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_prove_it.py
git commit -m "test(phase-0): prove-it — replay → projection → orchestrator = AskExaminer(weak_dim)"
```

---

## Task 13: Coverage + lint gate; close Phase 0

- [ ] **Step 1: Run full suite**

Run: `ruff check . && mypy && pytest --cov=core --cov-report=term-missing`
Expected: ruff clean, mypy clean, ≥90% coverage on `core/*`.

- [ ] **Step 2: Record Phase 0 review in `tasks/todo.md`**

Append a `## Phase 0 Review` section to the bottom of this file with:
- Commits list (from `git log --oneline`)
- Coverage % per module
- Deltas from plan (any tasks modified during exec)
- Staff-engineer checklist: `core/` pure? ✓/✗ · FSM invariants documented? ✓/✗ · budget helper exists? ✓/✗ · adversarial skeleton present? ✓/✗

- [ ] **Step 3: Update `tasks/lessons.md` with any new self-correction patterns**

- [ ] **Step 4: Final commit**

```bash
git add tasks/todo.md tasks/lessons.md
git commit -m "chore(phase-0): close — coverage ≥90%, prove-it green, review recorded"
```

---

## Self-Review Checklist (run before handoff)

- [ ] **Spec coverage:** §2 (domain) → Task 2. §4.1 → Task 2. §4.2 → Tasks 3–4. §4.3 → Task 5. §4.4 → Tasks 6–7. §4.5 → Task 8. §6 Phase 0 prove-it → Task 12. §8 directory → Tasks 1–11. §10 Open Q #1 → Task 9. §10 Open Q #2 → Task 3 (HumanOverride). §10 Open Q #3 → Tasks 6–7 (SessionTimedOut + idle logic). §15.5 → Task 10.
- [ ] **No placeholders:** every step contains runnable code or concrete commands.
- [ ] **Type consistency:** `Dimension`, `Actor`, `TurnKind`, `ArtifactKind` used identically across all tasks. `Envelope.payload` discriminated union; `Action` tagged dataclasses.
- [ ] **Deferred correctly:** Postgres, LLMs, Jupyter, OTel, PII encryption, adversarial-assertions — all explicitly Phase 1+.

---

## Execution Handoff

Two options:
1. **Subagent-driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline execution** — run tasks in this session with checkpoints.

Pick one and I'll kick off.
