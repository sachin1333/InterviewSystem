"""ProblemSequencer - manages the ordered list of Problems for a session.

FSM ordering invariants (2.1.6):
  1. ``ProblemIntroduced(N)`` must precede any Turn for that problem.
  2. ``ProblemClosed(N)`` must precede ``ProblemIntroduced(N+1)``.
  3. ``SessionEnded`` follows the last ``ProblemClosed`` - never before.

This module provides:
  ``ProblemSequencer``  - stateless service; given the planned problem list
                          and current projection state, decides the next
                          problem-boundary event to emit (if any).
  ``SequencerAction``   - discriminated union returned by ``next_event``.

The sequencer is *pure*: no I/O, no side effects.  The session_runner owns
writing to the event log.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.domain import Problem, ProblemId
from core.projections import _ProblemStatus, _SessionRecord

# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntroduceNext:
    """Emit ``ProblemIntroduced`` for the next problem in the sequence."""
    problem: Problem
    ordinal: int  # 1-based


@dataclass(frozen=True)
class EndSession:
    """All problems are closed - emit ``SessionEnded``."""
    reason: str = "all_problems_closed"


@dataclass(frozen=True)
class Noop:
    """Nothing to do right now (active problem still open)."""
    reason: str = "problem_active"


SequencerAction = IntroduceNext | EndSession | Noop


# ---------------------------------------------------------------------------
# Sequencer
# ---------------------------------------------------------------------------

class ProblemSequencer:
    """Decides the next problem-boundary action given the current session state.

    Parameters
    ----------
    problems:
        Ordered list of Problems planned for the session.  Determined once at
        session-start (drawn from ProblemBank).  Empty list is valid for legacy
        sessions that predate Phase 2.1 - the sequencer is a no-op in that
        case.
    """

    def __init__(self, problems: list[Problem]) -> None:
        self._problems = problems

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def next_event(self, record: _SessionRecord) -> SequencerAction:
        """Return the next sequencer action given the current session record.

        Called by ``SessionRunner`` after every state change.  Pure function
        (reads record, returns action, writes nothing).

        Decision tree
        -------------
        1. If no problems planned -> Noop (legacy / phase alpha session).
        2. If a problem is currently active -> Noop (let examiner run).
        3. If all problems have been closed (or list is exhausted) -> EndSession.
        4. Otherwise -> IntroduceNext with the first un-introduced problem.
        """
        if not self._problems:
            return Noop(reason="no_problems_planned")

        statuses = record["problem_status"]
        current_id = record["current_problem_id"]

        # Rule 2: an active problem is in flight - wait for it to close.
        if current_id is not None and statuses.get(current_id) == _ProblemStatus.active:
            return Noop(reason="problem_active")

        # Build sets for quick lookup.
        introduced_ids: set[ProblemId] = set(statuses.keys())
        closed_ids: set[ProblemId] = {
            pid for pid, st in statuses.items() if st == _ProblemStatus.closed
        }

        # Find the next problem that hasn't been introduced yet.
        for ordinal, problem in enumerate(self._problems, start=1):
            if problem.id not in introduced_ids:
                return IntroduceNext(problem=problem, ordinal=ordinal)

        # All problems were introduced.  Are they all closed?
        all_introduced_ids = {p.id for p in self._problems}
        if all_introduced_ids <= closed_ids:
            return EndSession()

        # Some problems introduced but not yet closed -> wait.
        return Noop(reason="awaiting_close")

    # ------------------------------------------------------------------
    # Invariant validation (called before appending to the log)
    # ------------------------------------------------------------------

    def validate_introduce(
        self,
        problem_id: ProblemId,
        record: _SessionRecord,
    ) -> None:
        """Raise ``ValueError`` if introducing *problem_id* would violate invariants.

        Invariants checked:
        - The problem must be in the planned list.
        - No other problem may currently be active.
        - The problem must not have been introduced already.
        """
        known_ids = {p.id for p in self._problems}
        if problem_id not in known_ids:
            raise ValueError(
                f"ProblemIntroduced: problem_id {problem_id!r} not in planned list"
            )

        statuses = record["problem_status"]
        current_id = record["current_problem_id"]
        if current_id is not None and statuses.get(current_id) == _ProblemStatus.active:
            raise ValueError(
                f"ProblemIntroduced: cannot introduce {problem_id!r} while "
                f"{current_id!r} is still active (invariant 2.1.6)"
            )

        if problem_id in statuses:
            raise ValueError(
                f"ProblemIntroduced: {problem_id!r} was already introduced"
            )

    def validate_close(
        self,
        problem_id: ProblemId,
        record: _SessionRecord,
    ) -> None:
        """Raise ``ValueError`` if closing *problem_id* would violate invariants.

        Invariants checked:
        - The problem must currently be active (not already closed).
        """
        statuses = record["problem_status"]
        status = statuses.get(problem_id)
        if status is None:
            raise ValueError(
                f"ProblemClosed: {problem_id!r} was never introduced"
            )
        if status == _ProblemStatus.closed:
            raise ValueError(
                f"ProblemClosed: {problem_id!r} is already closed"
            )
