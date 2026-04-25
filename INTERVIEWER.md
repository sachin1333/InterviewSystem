# Interviewer Guide — InterviewSystem

This document explains how to use this repository to run, test, and record technical interview sessions for candidates.

## Purpose

`InterviewSystem` is a lightweight, dependency-free Phase‑0 backbone for running structured interviews: domain types, an append-only event log, reducers (projections), an orchestrator, and a golden-sequence test. Use this workspace to run mock interviews, rehearse question flows, and record structured feedback.

## Quick prerequisites

- macOS or Linux
- Python 3.12+ (a local `.venv` is recommended)
- Homebrew (optional, used earlier to install `gh`)

## Setup (one-time)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Useful commands

- Run linters: `./.venv/bin/ruff check .`
- Run static types: `./.venv/bin/mypy --strict core`
- Run tests: `./.venv/bin/python -m pytest -q`
- Run the demo snippet that replays a golden event sequence (see below)

## Running a quick demo (local)

Run the following Python snippet inside the activated venv to replay a short "golden" event stream and show the orchestrator decision:

```python
from datetime import datetime, timezone
from core.eventlog import InMemoryEventLog
from core.events import Envelope, SessionStarted, TurnPosted, ArtifactAttached
from core.domain import Actor, TurnKind, ArtifactKind
from core.projections import SessionStore, ScoreStore
from core.orchestrator import next_action

now = lambda: datetime.now(timezone.utc)
log = InMemoryEventLog()
log.append(Envelope(session_id="demo", seq=1, at=now(), payload=SessionStarted(rubric_version="ds_mle@1")))
log.append(Envelope(session_id="demo", seq=2, at=now(), payload=TurnPosted(id="t1", actor=Actor.candidate, kind=TurnKind.answer)))
log.append(Envelope(session_id="demo", seq=3, at=now(), payload=ArtifactAttached(id="a1", kind=ArtifactKind.markdown, produced_by_turn_id="t1", version=1)))

sessions = SessionStore(); scores = ScoreStore()
for ev in log.get_session('demo'):
    sessions.apply(ev); scores.apply(ev)
print(next_action('demo', sessions, scores))
```

Expected output: `RequestScore(session_id='demo')` — the orchestrator recommends scoring when artifacts exist and no score is present.

## Where to find rubrics & tests

- Rubrics: `templates/rubrics/ds-ml-engineer-v1.yaml`
- Rubric loader: `core/rubric_loader.py` (loads YAML → `core.domain.Rubric`)
- Golden-sequence prove-it test: `tests/unit/test_prove_it.py`

## How to run an interview (recommended workflow)

1. Prepare the rubric and questions for the role under `templates/rubrics/` and `templates/agents/`.
2. Start the interview session (you can use the demo code above as a starting harness) and record events as `Envelope` objects into the `InMemoryEventLog`.
3. Replay the session into the `SessionStore` and `ScoreStore` to compute orchestrator suggestions (e.g., request scoring) and to persist turn/artifact metadata.
4. Use the `core.contracts` Protocols as stubs for integrating scoring, challenger, or runtime adapters when you build adapters.

## Recording feedback

Use a simple structured template (example):

- Candidate: <name>
- Role: <role>
- Date: <date>
- Rubric version: <rubric version>
- Per-dimension signals: (list dimension → score + confidence)
- Composite score: <0-1>
- Notes: <free-text feedback>

You may store feedback in a CSV, a database, or create a GitHub issue per candidate to record results and follow-ups.

## PRs and collaboration

- Branch from `phase0/backbone` for changes related to the scaffold.
- Run the CI baseline locally before pushing: `./.venv/bin/ruff check . && ./.venv/bin/mypy --strict core && ./.venv/bin/python -m pytest -q`

## Next steps (for interviewers who want to extend this)

- Implement adapters for real scoring services and a persistent event store (Postgres). 
- Add a lightweight CLI to spawn interactive interview sessions and persist recorded events. 
- Add more rubrics for other roles under `templates/rubrics/` and corresponding golden-sequence tests.

----
If you want, I can: (A) add a `INTERVIEW_TEMPLATE.md` file with candidate feedback fields, (B) scaffold a simple CLI to run an interview session, or (C) create an example integration with a fake scorer. Reply with A, B, or C.
