"""FSM-driven orchestrator. Pure function. No I/O.

Contract: see core/invariants.md for the state-transition table.
"""
from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from core.domain import Actor, ArtifactKind, Dimension, TurnKind
from core.projections import RuntimeStore, ScoreStore, SessionStore, SignalStore


class Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NoAction(Action):
    reason: str = "no-op"


class EndSession(Action):
    session_id: str
    reason: str


class RequestChallenge(Action):
    session_id: str


class RequestCandidateInput(Action):
    session_id: str
    kind: TurnKind


class RequestExecution(Action):
    session_id: str
    turn_id: str
    artifact_id: str


class RequestProbe(Action):
    session_id: str


class RequestScoring(Action):
    session_id: str
    artifact_id: str
    dimension: Dimension


class RequestAggregate(Action):
    session_id: str


ActionT = (
    NoAction
    | EndSession
    | RequestChallenge
    | RequestCandidateInput
    | RequestExecution
    | RequestProbe
    | RequestScoring
    | RequestAggregate
)


DEFAULT_SCORED_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension.problem_framing,
    Dimension.model_rationale,
    Dimension.insight_interp,
    Dimension.communication,
)


def next_action(
    session_id: str,
    sessions: SessionStore,
    scores: ScoreStore,
    signals: SignalStore | None = None,
    runtimes: RuntimeStore | None = None,
    scored_dimensions: Iterable[Dimension] = DEFAULT_SCORED_DIMENSIONS,
) -> ActionT:
    """Return the single next action for `session_id` per invariants.md.

    Pure: same snapshots in → same action out.
    """
    s = sessions.get(session_id)
    if s is None:
        return NoAction(reason="unknown-session")
    if s["ended"]:
        return NoAction(reason="session-ended")
    if scores.get(session_id) is not None:
        return EndSession(session_id=session_id, reason="scored")

    turns = s["turns"]
    prompt_turns = [
        t for t in turns
        if t["actor"] == Actor.challenger and t["kind"] == TurnKind.question
    ]

    # row 4: no prompt yet → ask challenger for one
    if not prompt_turns:
        return RequestChallenge(session_id=session_id)

    last = turns[-1]

    # row 5: last candidate turn produced a code cell that has not been run
    if last["actor"] == Actor.candidate and last["kind"] in (TurnKind.answer, TurnKind.defense):
        for aid, tid in s["artifact_turn"].items():
            if (
                tid == last["id"]
                and s["artifact_kind"][aid] == ArtifactKind.code_cell
                and (runtimes is None or not runtimes.has_run(session_id, last["id"]))
            ):
                return RequestExecution(
                    session_id=session_id, turn_id=last["id"], artifact_id=aid
                )

    # row 6/7: adapter just posted; candidate's turn to speak
    if last["actor"] in (Actor.challenger, Actor.examiner):
        kind = TurnKind.defense if last["kind"] == TurnKind.probe else TurnKind.answer
        return RequestCandidateInput(session_id=session_id, kind=kind)

    # last is candidate answer/defense with runtime (if any) already executed
    target_answers = s["target_answers"]
    max_probes = s["max_probes_per_prompt"]

    answer_count = sum(
        1 for t in turns
        if t["actor"] == Actor.candidate and t["kind"] == TurnKind.answer
    )

    last_prompt_seq = prompt_turns[-1]["seq"]
    probes_current = sum(
        1 for t in turns
        if t["actor"] == Actor.examiner
        and t["kind"] == TurnKind.probe
        and t["seq"] > last_prompt_seq
    )

    target_reached = answer_count >= target_answers

    # row 8: probe more on this prompt (probes are per-prompt, independent of
    # target; probing continues until budget exhausted, even if target already
    # reached on this last prompt)
    if probes_current < max_probes:
        return RequestProbe(session_id=session_id)

    # row 9a: probes exhausted, target not reached → advance to next prompt
    if not target_reached:
        return RequestChallenge(session_id=session_id)

    # row 9b / 10: target reached → scoring phase
    candidate_turn_ids = {
        t["id"] for t in turns if t["actor"] == Actor.candidate
    }
    scorable_artifacts = [
        aid for aid in s["artifact_ids"]
        if s["artifact_kind"][aid] != ArtifactKind.prompt
        and s["artifact_turn"].get(aid) in candidate_turn_ids
    ]

    if signals is not None:
        for dim in scored_dimensions:
            for aid in scorable_artifacts:
                if not signals.is_scored(session_id, aid, dim):
                    return RequestScoring(
                        session_id=session_id, artifact_id=aid, dimension=dim
                    )
        # row 12: all scorable (artifact, dim) pairs touched → aggregate
        if all(signals.has_any(session_id, dim) for dim in scored_dimensions):
            return RequestAggregate(session_id=session_id)
        return NoAction(reason="nothing-to-do")

    # signals projection not supplied: caller running without scoring wiring
    return NoAction(reason="nothing-to-do")
