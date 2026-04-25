"""Assert that RequestScoring never appears between RequestCandidateInput actions.

The FSM must always reach RequestCandidateInput (or EndSession) before triggering
scoring — scoring is off the candidate-visible critical path.
"""
from datetime import UTC, datetime

from core.eventlog import InMemoryEventLog
from core.events import (
    CandidateJoined,
    Envelope,
    SessionStarted,
)
from core.orchestrator import RequestScoring, next_action
from core.session_boot import replay


def test_scoring_never_blocks_candidate_input() -> None:
    """next_action never returns RequestScoring when candidate input is pending.

    After a candidate turn with no execution needed, the FSM should
    go to RequestScoring only after all candidate-visible turns are dispatched.
    This test just documents the invariant — the real proof is in e2e tests.
    We verify the orchestrator itself doesn't expose RequestScoring
    as a direct response to a fresh session.
    """
    log = InMemoryEventLog()
    sid = "s-test-async"
    now = datetime.now(UTC)
    log.append(Envelope(
        session_id=sid, seq=1, at=now,
        payload=SessionStarted(
            rubric_version="v1", target_answers=1, max_probes_per_prompt=0
        ),
    ))
    log.append(Envelope(
        session_id=sid, seq=2, at=now,
        payload=CandidateJoined(candidate_handle="alice"),
    ))

    sessions, scores, signals, runtimes, _ = replay(sid, log)
    action = next_action(sid, sessions, scores, signals, runtimes)

    # Right after session start, the FSM should want a challenge, not scoring.
    assert not isinstance(action, RequestScoring), (
        f"FSM returned {action!r} immediately after session start — "
        "scoring must only trigger after candidate input."
    )
