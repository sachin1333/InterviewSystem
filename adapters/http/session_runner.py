"""SessionRunner — higher-level adapter that drives the FSM interview loop.

Owns all event-log writes for challenger, examiner, scorer, and aggregator.
Called once per HTTP request (stateless: replays log at each step).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.examiner.llm_examiner import CoverageContext, LlmExaminer
from adapters.scorer._base import BaseLlmScorer
from adapters.scorer.aggregator import RubricAggregator
from core.case_loader import CaseDefinition, CaseStage
from core.contracts import EventLog
from core.coverage import CoverageTracker
from core.domain import (
    Actor,
    Artifact,
    ArtifactKind,
    Dimension,
    Problem,
    ProblemId,
    Score,
    Signal,
    TurnKind,
)
from core.events import (
    ArtifactAttached,
    CoverageSnapshot,
    Envelope,
    ExaminerFailed,
    PerProblemScoreComputed,
    ProblemClosed,
    ProblemIntroduced,
    ScoreComputed,
    ScorerFailed,
    SessionEnded,
    SignalEmitted,
    StageCompleted,
    StageEntered,
    TurnPosted,
    TurnTimingObserved,
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
from core.problem_bank import ProblemBank
from core.problem_sequencer import EndSession as SeqEndSession
from core.problem_sequencer import IntroduceNext, ProblemSequencer
from core.projections import ArtifactStore, RuntimeStore, ScoreStore, SessionStore, SignalStore
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
    problem_bank: ProblemBank | None = None
    # Legacy: kept for backward-compat with existing tests that pass a case;
    # StageEntered/StageCompleted are still emitted for analytics but the FSM
    # no longer branches on them when `problems` is set.
    case: CaseDefinition | None = None
    # Phase 2.2: per-problem probe safety cap.
    max_probes_per_problem: int = 6

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def advance(self, session_id: str, log: EventLog) -> RunResult:
        """Step the FSM until `RequestCandidateInput`, `EndSession`, or `NoAction`."""
        stage_seq = self.case.stages if self.case is not None else ()
        planned_problems = self._planned_problems(session_id)
        sequencer = ProblemSequencer(planned_problems) if planned_problems else None

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


    def _planned_problems(self, session_id: str) -> list[Problem]:
        """Return the problem sequence for this session.

        Explicit ``problems`` preserves test/backward-compatible injection. When
        absent, Phase 2.3 draws a deterministic sequence from ``problem_bank``.
        """
        if self.problems:
            return list(self.problems)
        if self.problem_bank is None:
            return []
        return self.problem_bank.pick_sequence(session_id)

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
        started = time.monotonic()
        problem = action.problem
        context_assembled_ms = _elapsed_ms(started)
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
        first_paint_ms = _elapsed_ms(started)
        self._append(session_id, log, TurnTimingObserved(
            turn_id=turn_id,
            phase="challenger_opener",
            submit_received_ms=0,
            context_assembled_ms=context_assembled_ms,
            first_token_ms=context_assembled_ms,
            first_paint_ms=first_paint_ms,
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
                reason=reason,
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
        """Issue an examiner probe or close the current problem (Phase 2.2).

        1. Build CoverageTracker from SignalEmitted events in the log.
        2. Check per-problem safety cap: force close if at/over max_probes.
        3. Call LlmExaminer with coverage context + problem-scoped transcript.
        4. If examiner returns ``close`` → emit ProblemClosed; return without
           posting a probe turn (ProblemSequencer will drive the next action).
        5. If examiner returns ``probe`` → emit CoverageSnapshot, post turn +
           artifact.
        6. Fallback (no examiner, or failure) → post a probe turn with no text
           so the orchestrator's probe budget still drains.
        """
        started = time.monotonic()
        sessions, _, _, _, _ = _replay_minimal(session_id, log)
        s = sessions.get(session_id)
        current_problem_id: str | None = s["current_problem_id"] if s else None

        # ── Build coverage tracker from event log ──
        tracker = _build_coverage_tracker(session_id, log)

        # ── Count probes for current problem ──
        probe_count = _count_probes_for_problem(session_id, log, current_problem_id)

        # ── Safety cap: force close if probe budget exhausted ──
        if current_problem_id is not None and probe_count >= self.max_probes_per_problem:
            self._do_close_problem(
                session_id, log,
                problem_id=current_problem_id,
                reason="max_probes",
                rationale=f"Safety cap reached ({probe_count}/{self.max_probes_per_problem} probes).",
            )
            return

        # ── Build coverage context for examiner ──
        problem_obj = _find_problem(self._planned_problems(session_id), current_problem_id)
        thresholds = problem_obj.dim_thresholds if problem_obj else {}
        under_served = (
            tracker.under_served(ProblemId(current_problem_id), thresholds)
            if current_problem_id else list(Dimension)
        )
        signal_map = (
            tracker.signal_map(ProblemId(current_problem_id))
            if current_problem_id else {}
        )
        transcript = _build_problem_transcript(session_id, log, current_problem_id)

        coverage = CoverageContext(
            problem_id=current_problem_id or "",
            under_served_dims=tuple(d.value for d in under_served),
            signal_map={d.value: v for d, v in signal_map.items()},
            probe_count=probe_count,
            max_probes=self.max_probes_per_problem,
            problem_transcript=transcript,
        )
        context_assembled_ms = _elapsed_ms(started)

        # ── Ask examiner ──
        failure: ExaminerFailed | None = None
        if self.examiner is not None:
            outcome, failure = self.examiner.review(session_id, [], coverage=coverage)

            if outcome.action == "close" and current_problem_id is not None:
                self._do_close_problem(
                    session_id, log,
                    problem_id=current_problem_id,
                    reason=outcome.close_reason or "examiner_pivot",
                    rationale=outcome.rationale,
                )
                return

            # Emit CoverageSnapshot before posting probe turn (task 2.2.4)
            if current_problem_id is not None:
                self._append(session_id, log, CoverageSnapshot(
                    problem_id=ProblemId(current_problem_id),
                    probe_count=probe_count,
                    signal_map={d.value: v for d, v in signal_map.items()},
                    under_served=[d.value for d in under_served],
                ))

            probe_text: str | None = outcome.probe_text if not outcome.ok_to_advance else None
        else:
            probe_text = None
        first_token_ms = _elapsed_ms(started) if probe_text else context_assembled_ms

        # ── Post probe turn ──
        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        self._append(session_id, log, TurnPosted(
            id=turn_id, actor=Actor.examiner, kind=TurnKind.probe
        ))
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
        self._append(session_id, log, TurnTimingObserved(
            turn_id=turn_id,
            phase="examiner_probe",
            submit_received_ms=0,
            context_assembled_ms=context_assembled_ms,
            first_token_ms=first_token_ms,
            first_paint_ms=_elapsed_ms(started),
        ))

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
        # CoverageTracker is rebuilt from SignalEmitted events on each request
        # (event-sourced): no in-memory update needed here.

    def _do_aggregate(self, session_id: str, log: EventLog) -> None:
        all_signals: list[Signal] = []
        envelopes = log.get_session(session_id)
        for env in envelopes:
            if isinstance(env.payload, SignalEmitted):
                all_signals.append(env.payload.signal)

        result = self.aggregator.aggregate(session_id, all_signals)
        self._append(session_id, log, ScoreComputed(score=result.score))
        for problem_score in _per_problem_scores(session_id, envelopes, all_signals, self.aggregator):
            self._append(session_id, log, problem_score)
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


# ------------------------------------------------------------------ #
#  Module-level helpers (pure, no class dependency)                  #
# ------------------------------------------------------------------ #

def _per_problem_scores(
    session_id: str,
    envelopes: tuple[Envelope, ...],
    signals: list[Signal],
    aggregator: RubricAggregator,
) -> list[PerProblemScoreComputed]:
    turn_problem: dict[str, tuple[ProblemId, int]] = {}
    artifact_turn: dict[str, str] = {}
    active_problem: tuple[ProblemId, int] | None = None

    for env in envelopes:
        payload = env.payload
        if isinstance(payload, ProblemIntroduced):
            active_problem = (payload.problem_id, payload.ordinal)
        elif isinstance(payload, ProblemClosed):
            if active_problem is not None and active_problem[0] == payload.problem_id:
                active_problem = None
        elif isinstance(payload, TurnPosted) and active_problem is not None:
            turn_problem[payload.id] = active_problem
        elif isinstance(payload, ArtifactAttached):
            artifact_turn[payload.id] = payload.produced_by_turn_id

    grouped: dict[tuple[ProblemId, int], list[Signal]] = {}
    for signal in signals:
        seen: set[tuple[ProblemId, int]] = set()
        for ref in signal.source_refs:
            turn_id = artifact_turn.get(ref)
            if turn_id is None:
                continue
            problem_key = turn_problem.get(turn_id)
            if problem_key is not None:
                seen.add(problem_key)
        for problem_key in seen:
            grouped.setdefault(problem_key, []).append(signal)

    out: list[PerProblemScoreComputed] = []
    for (problem_id, ordinal), problem_signals in sorted(grouped.items(), key=lambda item: item[0][1]):
        score = _score_signals_without_feedback(session_id, problem_signals, aggregator)
        if score.per_dimension:
            out.append(
                PerProblemScoreComputed(
                    problem_id=problem_id,
                    ordinal=ordinal,
                    score=score,
                )
            )
    return out


def _score_signals_without_feedback(
    session_id: str,
    signals: list[Signal],
    aggregator: RubricAggregator,
) -> Score:
    per_dimension: dict[Dimension, float] = {}
    weighted_total = 0.0
    included_weight = 0.0
    for dimension, weight in aggregator.rubric.weights.items():
        dimension_signals = [signal for signal in signals if signal.dimension == dimension]
        if len(dimension_signals) < aggregator.min_signals_per_dimension:
            continue
        dimension_score = aggregator._dimension_score(dimension_signals)
        if dimension_score is None:
            continue
        rounded = round(dimension_score, 4)
        per_dimension[dimension] = rounded
        weighted_total += rounded * weight
        included_weight += weight
    composite = round(weighted_total / included_weight, 4) if included_weight else 0.0
    return Score(
        session_id=session_id,
        rubric_version=aggregator.rubric.version,
        per_dimension=per_dimension,
        composite=composite,
        at=datetime.now(UTC),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _replay_minimal(
    session_id: str,
    log: EventLog,
) -> tuple[SessionStore, ScoreStore, SignalStore, RuntimeStore, ArtifactStore]:
    """Thin wrapper so _do_probe can get SessionStore without circular import."""
    from core.session_boot import replay
    return replay(session_id, log)


def _build_coverage_tracker(session_id: str, log: EventLog) -> CoverageTracker:
    """Rebuild CoverageTracker from SignalEmitted events (event-sourced)."""
    tracker = CoverageTracker()
    for env in log.get_session(session_id):
        if isinstance(env.payload, SignalEmitted):
            sig = env.payload.signal
            # Attribute signal to current_problem_id at time of emission.
            # We don't have per-signal problem tagging yet, so we use a
            # best-effort approach: check CoverageSnapshot for problem context.
            # For now, accumulate all signals under the session-level key.
            # Phase 2.3 will add problem_id to SignalEmitted.
            pass  # tracker populated below via per-problem attribution
    # Per-problem attribution via ProblemIntroduced / ProblemClosed brackets:
    current_pid: str | None = None
    for env in log.get_session(session_id):
        if isinstance(env.payload, ProblemIntroduced):
            current_pid = env.payload.problem_id
        elif isinstance(env.payload, ProblemClosed):
            current_pid = None
        elif isinstance(env.payload, SignalEmitted) and current_pid is not None:
            sig = env.payload.signal
            tracker.record(ProblemId(current_pid), sig.dimension, sig.value)
    return tracker


def _count_probes_for_problem(
    session_id: str, log: EventLog, problem_id: str | None
) -> int:
    """Count TurnPosted(examiner, probe) events under the active problem bracket."""
    if problem_id is None:
        return 0
    count = 0
    inside = False
    for env in log.get_session(session_id):
        if isinstance(env.payload, ProblemIntroduced):
            if env.payload.problem_id == problem_id:
                inside = True
        elif isinstance(env.payload, ProblemClosed):
            if env.payload.problem_id == problem_id:
                inside = False
        elif (
            inside
            and isinstance(env.payload, TurnPosted)
            and env.payload.actor == Actor.examiner
            and env.payload.kind == TurnKind.probe
        ):
            count += 1
    return count


def _find_problem(problems: list[Problem], problem_id: str | None) -> Problem | None:
    if problem_id is None:
        return None
    for p in problems:
        if p.id == problem_id:
            return p
    return None


def _build_problem_transcript(
    session_id: str, log: EventLog, problem_id: str | None
) -> str:
    """Build a text transcript scoped to the active problem only.

    Includes challenger/examiner questions and candidate answers,
    using ArtifactAttached content for turn text.
    """
    if problem_id is None:
        return ""

    # Collect artifact content map
    artifact_content: dict[str, str] = {}
    for env in log.get_session(session_id):
        if isinstance(env.payload, ArtifactAttached) and env.payload.content:
            artifact_content[env.payload.id] = env.payload.content

    # Map turn_id → artifact content
    turn_artifact: dict[str, str] = {}
    for env in log.get_session(session_id):
        if isinstance(env.payload, ArtifactAttached) and env.payload.content:
            turn_artifact[env.payload.produced_by_turn_id] = env.payload.content

    lines: list[str] = []
    inside = False
    for env in log.get_session(session_id):
        if isinstance(env.payload, ProblemIntroduced):
            if env.payload.problem_id == problem_id:
                inside = True
        elif isinstance(env.payload, ProblemClosed):
            if env.payload.problem_id == problem_id:
                inside = False
        elif inside and isinstance(env.payload, TurnPosted):
            tp = env.payload
            actor_label = {
                Actor.challenger: "Interviewer",
                Actor.examiner: "Examiner",
                Actor.candidate: "Candidate",
            }.get(tp.actor, tp.actor.value)
            text = turn_artifact.get(tp.id, "")
            if text:
                lines.append(f"{actor_label}: {text}")
    return "\n".join(lines)
