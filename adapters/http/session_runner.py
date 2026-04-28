"""SessionRunner — higher-level adapter that drives the FSM interview loop.

Owns all event-log writes for challenger, examiner, scorer, and aggregator.
Called once per HTTP request (stateless: replays log at each step).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.examiner.llm_examiner import LlmExaminer
from adapters.scorer._base import BaseLlmScorer
from adapters.scorer.aggregator import RubricAggregator
from core.case_loader import CaseDefinition, CaseStage
from core.contracts import EventLog
from core.domain import Actor, Artifact, ArtifactKind, Dimension, Problem, Signal, TurnKind
from core.events import (
    ArtifactAttached,
    Envelope,
    ExaminerFailed,
    ProblemClosed,
    ProblemIntroduced,
    ScoreComputed,
    ScorerFailed,
    SessionEnded,
    SignalEmitted,
    StageCompleted,
    StageEntered,
    TurnPosted,
)
from core.orchestrator import (
    DEFAULT_SCORED_DIMENSIONS,
    EndSession,
    NoAction,
    RequestAggregate,
    RequestCandidateInput,
    RequestChallenge,
    RequestExecution,
    RequestProbe,
    RequestScoring,
    next_action,
)
from core.problem_sequencer import EndSession as SeqEndSession
from core.problem_sequencer import IntroduceNext, ProblemSequencer
from core.projections import ArtifactStore, SessionStore
from core.session_boot import replay_with_stages

_MAX_STEPS = 100


@dataclass(frozen=True)
class RunResult:
    """What the HTTP handler should render after advancing the FSM."""

    state: Literal["await_input", "ended", "no_op"]
    session_id: str
    display_text: str | None = None
    turn_kind: TurnKind | None = None


@dataclass
class SessionRunner:
    """Stateless FSM driver.  Each call to :meth:`advance` is independent."""

    challenger: LlmChallenger
    aggregator: RubricAggregator
    examiner: LlmExaminer | None = None
    scorers: dict[Dimension, BaseLlmScorer] = field(default_factory=dict)
    scored_dimensions: tuple[Dimension, ...] = DEFAULT_SCORED_DIMENSIONS
    execution_timeout: float = 10.0
    # Phase 2.1: multi-problem sequence replaces the single CaseDefinition.
    # When non-empty, ProblemSequencer drives problem boundaries instead of
    # the legacy StageEntered/StageCompleted FSM.
    problems: list[Problem] = field(default_factory=list)
    # Legacy: kept for backward-compat with existing tests that pass a case;
    # StageEntered/StageCompleted are still emitted for analytics but the FSM
    # no longer branches on them when `problems` is set.
    case: CaseDefinition | None = None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def advance(self, session_id: str, log: EventLog) -> RunResult:
        """Step the FSM until `RequestCandidateInput`, `EndSession`, or `NoAction`."""
        stage_seq = self.case.stages if self.case is not None else ()
        sequencer = ProblemSequencer(self.problems) if self.problems else None

        for _ in range(_MAX_STEPS):
            sessions, scores, signals, runtimes, artifacts, stages = replay_with_stages(
                session_id, log, stage_sequence=stage_seq
            )

            # ── Phase 2.1: problem-boundary loop (takes priority over stage FSM) ──
            if sequencer is not None:
                s = sessions.get(session_id)
                if s is not None:
                    seq_action = sequencer.next_event(s)
                    if isinstance(seq_action, IntroduceNext):
                        self._do_introduce_problem(session_id, log, seq_action)
                        continue
                    if isinstance(seq_action, SeqEndSession):
                        # All problems closed — proceed to scoring/aggregation via
                        # the orchestrator (fall through to next_action below).
                        pass
                    # Noop: active problem in flight, let orchestrator drive turns.

            # ── Legacy multi-stage FSM: only runs when `problems` list is empty ──
            # Stage events are kept for analytics on legacy sessions; the hot
            # path for new sessions uses ProblemSequencer above.
            elif self.case is not None:
                cur_id = stages.current_id(session_id)
                if cur_id is not None and cur_id not in stages.completed_ids(session_id):
                    s = sessions.get(session_id)
                    if s is not None:
                        stage_entered_seq = next(
                            (
                                env.seq
                                for env in log.get_session(session_id)
                                if getattr(env.payload, "stage_id", None) == cur_id
                                and isinstance(env.payload, StageEntered)
                            ),
                            0,
                        )
                        answers_since = sum(
                            1
                            for t in s["turns"]
                            if t["actor"] == Actor.candidate
                            and t["seq"] > stage_entered_seq
                        )
                        if answers_since >= 1:
                            self._append(session_id, log, StageCompleted(stage_id=cur_id))
                            continue

                if not stages.all_stages_completed(session_id):
                    next_stage_obj = stages.next_after(session_id)
                    if next_stage_obj is not None:
                        cur = stages.current_id(session_id)
                        if cur is None or cur in stages.completed_ids(session_id):
                            self._do_stage_challenge(session_id, log, next_stage_obj)
                            continue

            action = next_action(
                session_id, sessions, scores, signals, runtimes,
                scored_dimensions=self.scored_dimensions,
            )

            if isinstance(action, RequestCandidateInput):
                text = self._last_system_text(session_id, sessions, artifacts)
                return RunResult(
                    state="await_input",
                    session_id=session_id,
                    display_text=text,
                    turn_kind=action.kind,
                )

            if isinstance(action, EndSession):
                return RunResult(state="ended", session_id=session_id)

            if isinstance(action, NoAction):
                s = sessions.get(session_id)
                if s is not None and s["ended"]:
                    return RunResult(state="ended", session_id=session_id)
                return RunResult(state="no_op", session_id=session_id)

            if isinstance(action, RequestChallenge):
                if self.case is not None:
                    # In multi-stage mode, challenge is handled by _do_stage_challenge above.
                    # This branch is only reached when all stages are complete — skip.
                    pass
                else:
                    self._do_challenge(session_id, log)

            elif isinstance(action, RequestProbe):
                self._do_probe(session_id, log)

            elif isinstance(action, RequestExecution):
                self._do_execution(session_id, log, action.turn_id, action.artifact_id, artifacts)

            elif isinstance(action, RequestScoring):
                self._do_scoring(
                    session_id, log, action.artifact_id, action.dimension,
                    sessions, artifacts,
                )

            elif isinstance(action, RequestAggregate):
                self._do_aggregate(session_id, log)
                return RunResult(state="ended", session_id=session_id)

        return RunResult(state="no_op", session_id=session_id)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _append(
        self,
        session_id: str,
        log: EventLog,
        payload: object,
        *,
        idem_key: str | None = None,
    ) -> Envelope:
        seq = (log.last_seq(session_id) or 0) + 1
        env = Envelope(
            session_id=session_id,
            seq=seq,
            at=datetime.now(UTC),
            payload=payload,
            idem_key=idem_key,
        )
        return log.append(env, idem_key)

    def _do_introduce_problem(
        self, session_id: str, log: EventLog, action: IntroduceNext
    ) -> None:
        """Emit ``ProblemIntroduced`` and a challenger turn with the opener text."""
        problem = action.problem
        self._append(
            session_id, log,
            ProblemIntroduced(
                problem_id=problem.id,
                opener_text=problem.opener_text,
                ordinal=action.ordinal,
            ),
        )
        # Post the opener as a challenger turn so the UI can render it.
        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        artifact_id = f"a-{uuid.uuid4().hex[:8]}"
        self._append(session_id, log, TurnPosted(
            id=turn_id, actor=Actor.challenger, kind=TurnKind.question
        ))
        self._append(session_id, log, ArtifactAttached(
            id=artifact_id,
            kind=ArtifactKind.prompt,
            produced_by_turn_id=turn_id,
            version=1,
            content=problem.opener_text,
        ))

    def _do_close_problem(
        self,
        session_id: str,
        log: EventLog,
        problem_id: str,
        reason: str = "examiner_pivot",
        rationale: str = "",
    ) -> None:
        """Emit ``ProblemClosed``.  Called by the examiner path in Phase 2.2."""
        from core.domain import ProblemId  # local import; avoids circular at top
        self._append(
            session_id, log,
            ProblemClosed(
                problem_id=ProblemId(problem_id),
                reason=reason,  # type: ignore[arg-type]
                rationale=rationale,
            ),
        )

    def _do_challenge(self, session_id: str, log: EventLog) -> None:
        prompts = list(self.challenger.propose_prompts(session_id))
        prompt_text = prompts[0] if prompts else "(no question generated)"
        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        artifact_id = f"a-{uuid.uuid4().hex[:8]}"
        self._append(session_id, log, TurnPosted(id=turn_id, actor=Actor.challenger, kind=TurnKind.question))
        self._append(session_id, log, ArtifactAttached(
            id=artifact_id,
            kind=ArtifactKind.prompt,
            produced_by_turn_id=turn_id,
            version=1,
            content=prompt_text,
        ))

    def _do_stage_challenge(self, session_id: str, log: EventLog, stage: CaseStage) -> None:
        """Issue a challenger turn for the given CaseStage and mark it as entered."""
        stage_id: str = stage.id
        primitive_val: str = stage.primitive.value
        prompt_seed: str = stage.prompt_seed

        # First stage uses the case_bank if available; subsequent stages use prompt_seed.
        if (
            self.case is not None
            and self.case.stages
            and stage_id == self.case.stages[0].id
            and self.challenger.case_bank is not None
        ):
            prompt_text = self.challenger.case_bank.pick_for_session(session_id).body
        elif prompt_seed:
            prompt_text = prompt_seed
        else:
            prompts = list(self.challenger.propose_prompts(session_id))
            prompt_text = prompts[0] if prompts else "(no question generated)"

        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        artifact_id = f"a-{uuid.uuid4().hex[:8]}"
        self._append(session_id, log, StageEntered(stage_id=stage_id, primitive=primitive_val))
        self._append(session_id, log, TurnPosted(id=turn_id, actor=Actor.challenger, kind=TurnKind.question))
        self._append(session_id, log, ArtifactAttached(
            id=artifact_id,
            kind=ArtifactKind.prompt,
            produced_by_turn_id=turn_id,
            version=1,
            content=prompt_text,
        ))

    def _do_probe(self, session_id: str, log: EventLog) -> None:
        """Issue an examiner probe.

        If examiner is absent or returns ok_to_advance, emits a synthetic
        TurnPosted(examiner, probe) with no artifact to consume the probe
        budget.  The candidate will see the last question text and be asked
        for a defense turn.
        """
        probe_text: str | None = None
        failure: ExaminerFailed | None = None

        if self.examiner is not None:
            # Call examiner with empty recent_turns (simplified for Phase ε).
            outcome, failure = self.examiner.review(session_id, [])
            if not outcome.ok_to_advance and outcome.probe_text:
                probe_text = outcome.probe_text

        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        self._append(session_id, log, TurnPosted(id=turn_id, actor=Actor.examiner, kind=TurnKind.probe))
        if probe_text:
            artifact_id = f"a-{uuid.uuid4().hex[:8]}"
            self._append(session_id, log, ArtifactAttached(
                id=artifact_id,
                kind=ArtifactKind.prompt,
                produced_by_turn_id=turn_id,
                version=1,
                content=probe_text,
            ))
        if failure is not None:
            self._append(session_id, log, failure)

    def _do_execution(
        self,
        session_id: str,
        log: EventLog,
        turn_id: str,
        artifact_id: str,
        artifacts: ArtifactStore,
    ) -> None:
        code = artifacts.get(artifact_id) or ""
        from adapters.runtime.runtime_adapter import RuntimeAdapter  # deferred to avoid cycles
        RuntimeAdapter(log).execute(session_id, turn_id, code, timeout_seconds=self.execution_timeout)

    def _do_scoring(
        self,
        session_id: str,
        log: EventLog,
        artifact_id: str,
        dimension: Dimension,
        sessions: SessionStore,
        artifacts: ArtifactStore,
    ) -> None:
        s = sessions.get(session_id)
        body = artifacts.get(artifact_id) or ""
        artifact_kind = s["artifact_kind"].get(artifact_id, ArtifactKind.markdown) if s else ArtifactKind.markdown
        turn_id = s["artifact_turn"].get(artifact_id, "unknown") if s else "unknown"
        artifact = Artifact(
            id=artifact_id,
            kind=artifact_kind,
            version=1,
            body=body,
            produced_by_turn_id=turn_id,
            at=datetime.now(UTC),
        )

        scorer = self.scorers.get(dimension)
        if scorer is None:
            # No scorer for this dim — emit a zero-confidence placeholder so
            # is_scored() becomes True and the loop terminates.
            dummy = Signal(
                dimension=dimension,
                value=0.0,
                confidence=0.0,
                source_refs=(artifact_id,),
                emitted_by="session_runner:no_scorer",
                at=datetime.now(UTC),
            )
            self._append(session_id, log, ScorerFailed(dimension=dimension, reason="no scorer configured"))
            self._append(session_id, log, SignalEmitted(signal=dummy))
            return

        result = scorer.score_optional(session_id, artifact)

        if result.failure is not None:
            self._append(session_id, log, ScorerFailed(
                dimension=dimension, reason=result.failure.reason,
            ))

        signal = result.signal
        if signal is None:
            signal = scorer._heuristic_signal(artifact)

        # Ensure source_refs contains the RAW artifact_id (no prefix) so that
        # SignalStore.is_scored(session_id, artifact_id, dim) returns True.
        # The heuristic scorer uses "artifact://{id}" which doesn't match.
        if artifact_id not in signal.source_refs:
            signal = Signal(
                dimension=signal.dimension,
                value=signal.value,
                confidence=signal.confidence,
                source_refs=(artifact_id,),
                emitted_by=signal.emitted_by,
                at=signal.at,
            )

        self._append(session_id, log, SignalEmitted(signal=signal))

    def _do_aggregate(self, session_id: str, log: EventLog) -> None:
        all_signals: list[Signal] = []
        for env in log.get_session(session_id):
            if isinstance(env.payload, SignalEmitted):
                all_signals.append(env.payload.signal)

        result = self.aggregator.aggregate(session_id, all_signals)
        self._append(session_id, log, ScoreComputed(score=result.score))
        self._append(session_id, log, SessionEnded(reason="scored"))

    def _last_system_text(
        self,
        session_id: str,
        sessions: SessionStore,
        artifacts: ArtifactStore,
    ) -> str | None:
        s = sessions.get(session_id)
        if s is None:
            return None
        # Find last challenger/examiner turn that has content.
        for turn in reversed(s["turns"]):
            if turn["actor"] in (Actor.challenger, Actor.examiner):
                for aid, tid in s["artifact_turn"].items():
                    if tid == turn["id"]:
                        content = artifacts.get(aid)
                        if content is not None:
                            return content
        return None
