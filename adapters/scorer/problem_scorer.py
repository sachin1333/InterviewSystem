from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any, ClassVar, Literal, Protocol, cast

from adapters.llm.structured_schema import scoring_result_schema
from adapters.llm.task_tier_map import tier_for
from core.domain import Artifact, Dimension, ProblemId, Signal
from core.events import ScorerFailed
from core.prompt_safety import RED_LINE, candidate_turn_envelope


class ProblemJsonModelRouter(Protocol):
    def call_json(
        self,
        *,
        tier: Literal["cheap", "mid", "top"],
        prompt: str,
        schema: set[str] | Mapping[str, object],
        deadline_ms: int | None = None,
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ProblemScoringResult:
    signals: tuple[Signal, ...]
    failures: tuple[ScorerFailed, ...]


@dataclass(frozen=True)
class _ParsedSignal:
    signal: Signal | None
    failure: ScorerFailed | None


class ProblemLlmScorer:
    scorer_name = "problem_scorer"

    _DIMENSION_KEYWORDS: ClassVar[dict[Dimension, tuple[str, ...]]] = {
        Dimension.problem_framing: (
            "problem",
            "goal",
            "user",
            "objective",
            "scope",
            "constraint",
        ),
        Dimension.model_rationale: (
            "because",
            "tradeoff",
            "baseline",
            "assumption",
            "rationale",
        ),
        Dimension.experiment_design: (
            "experiment",
            "metric",
            "holdout",
            "ab test",
            "guardrail",
        ),
        Dimension.insight_interp: (
            "insight",
            "interpret",
            "implies",
            "segment",
            "trend",
        ),
        Dimension.communication: (
            "recommend",
            "summary",
            "therefore",
            "stakeholder",
            "clear",
        ),
        Dimension.response_authenticity: (
            "i built",
            "we handled",
            "my project",
            "specific",
            "counterfactual",
        ),
    }

    def __init__(self, model_router: ProblemJsonModelRouter) -> None:
        self.model_router = model_router

    def score_problem(
        self,
        session_id: str,
        problem_id: ProblemId,
        problem_context: str,
        candidate_artifacts: tuple[Artifact, ...],
        dimensions: tuple[Dimension, ...],
    ) -> ProblemScoringResult:
        if not dimensions:
            return ProblemScoringResult(signals=(), failures=())

        try:
            payload = self.model_router.call_json(
                tier=tier_for("scorer.problem"),
                prompt=self._compose_prompt(
                    session_id,
                    problem_id,
                    problem_context,
                    candidate_artifacts,
                    dimensions,
                ),
                schema={"signals"},
                json_schema=scoring_result_schema(tuple(dim.value for dim in dimensions)),
                schema_name="problem_scoring_result",
                max_completion_tokens=700,
            )
        except Exception as exc:
            return self._fallback_all(dimensions, candidate_artifacts, self._reason(exc))

        raw_signals = payload.get("signals")
        if not isinstance(raw_signals, list):
            return self._fallback_all(
                dimensions,
                candidate_artifacts,
                "invalid scorer payload: signals must be a list",
            )

        by_dimension: dict[Dimension, Signal] = {}
        failures: list[ScorerFailed] = []
        requested = set(dimensions)
        artifact_ids = tuple(artifact.id for artifact in candidate_artifacts)
        artifact_id_set = set(artifact_ids)

        for raw_signal in raw_signals:
            parsed = self._parse_signal(raw_signal, requested, artifact_ids, artifact_id_set)
            if parsed.failure is not None:
                failures.append(parsed.failure)
            if parsed.signal is not None and parsed.signal.dimension not in by_dimension:
                by_dimension[parsed.signal.dimension] = parsed.signal

        failed_dimensions = {failure.dimension for failure in failures}
        signals: list[Signal] = []
        for dimension in dimensions:
            signal = by_dimension.get(dimension)
            if signal is None:
                signals.append(self._heuristic_signal(dimension, candidate_artifacts))
                if dimension not in failed_dimensions:
                    failures.append(
                        ScorerFailed(dimension=dimension, reason="missing scorer signal")
                    )
            else:
                signals.append(signal)

        return ProblemScoringResult(signals=tuple(signals), failures=tuple(failures))

    def _compose_prompt(
        self,
        session_id: str,
        problem_id: ProblemId,
        problem_context: str,
        candidate_artifacts: tuple[Artifact, ...],
        dimensions: tuple[Dimension, ...],
    ) -> str:
        artifact_sections = []
        for artifact in candidate_artifacts:
            artifact_sections.extend(
                [
                    f"Artifact ID: {artifact.id}",
                    f"Artifact kind: {artifact.kind}",
                    candidate_turn_envelope(
                        artifact.produced_by_turn_id,
                        "candidate",
                        artifact.body,
                    ),
                ]
            )
        return "\n\n".join(
            [
                "Score this problem response across all requested dimensions in one JSON object.",
                f"Session: {session_id}",
                f"Problem ID: {problem_id}",
                "Dimensions: " + ", ".join(dim.value for dim in dimensions),
                RED_LINE,
                "Problem context:",
                problem_context,
                "Candidate artifacts:",
                "\n".join(artifact_sections),
                "Use only plain candidate artifact IDs in source_refs; do not use artifact:// URIs.",
            ]
        )

    def _parse_signal(
        self,
        raw_signal: object,
        requested: set[Dimension],
        artifact_ids: tuple[str, ...],
        artifact_id_set: set[str],
    ) -> _ParsedSignal:
        if not isinstance(raw_signal, dict):
            return self._malformed_all("scorer signal must be a mapping", requested)

        raw_dimension = raw_signal.get("dimension")
        if not isinstance(raw_dimension, str):
            return self._malformed_all("scorer signal missing dimension", requested)
        try:
            dimension = Dimension(raw_dimension)
        except ValueError:
            return self._malformed_all(f"unknown scorer dimension: {raw_dimension}", requested)
        if dimension not in requested:
            return self._malformed_all(
                f"unexpected scorer dimension: {dimension.value}", requested
            )

        try:
            value = self._strict_score(raw_signal.get("value"), "value")
            confidence = self._strict_score(raw_signal.get("confidence"), "confidence")
            source_refs = self._source_refs(raw_signal.get("source_refs"), artifact_ids, artifact_id_set)
        except ValueError as exc:
            return _ParsedSignal(
                signal=None,
                failure=ScorerFailed(dimension=dimension, reason=self._reason(exc)),
            )

        justification = str(
            raw_signal.get("justification")
            or raw_signal.get("rationale")
            or f"Problem scorer evaluated {dimension.value} from candidate artifacts."
        ).strip()
        if not justification:
            justification = f"Problem scorer evaluated {dimension.value} from candidate artifacts."

        return _ParsedSignal(
            signal=Signal(
                id=f"sig-problem-{dimension.value}-{self._source_suffix(source_refs)}",
                dimension=dimension,
                value=value,
                confidence=confidence,
                source_refs=source_refs,
                emitted_by=self.scorer_name,
                justification=justification,
                at=datetime.now(UTC),
            ),
            failure=None,
        )

    def _malformed_all(self, reason: str, requested: set[Dimension]) -> _ParsedSignal:
        # The malformed signal cannot be trusted for any single requested dimension.
        # Attach the first deterministic dimension to keep failure events schema-valid.
        dimension = sorted(requested, key=lambda dim: dim.value)[0]
        return _ParsedSignal(signal=None, failure=ScorerFailed(dimension=dimension, reason=reason))

    @staticmethod
    def _strict_score(raw_value: object, field: str) -> float:
        if type(raw_value) not in (int, float):
            raise ValueError(f"{field} must be a number")
        value = float(cast(int | float, raw_value))
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError(f"{field} must be within [0, 1]")
        return value

    @staticmethod
    def _source_refs(
        raw_refs: object,
        artifact_ids: tuple[str, ...],
        artifact_id_set: set[str],
    ) -> tuple[str, ...]:
        if raw_refs is None:
            return artifact_ids
        if not isinstance(raw_refs, list):
            raise ValueError("source_refs must be a list when provided")

        refs: list[str] = []
        for raw_ref in raw_refs:
            ref = str(raw_ref)
            if ref.startswith("artifact://"):
                ref = ref.removeprefix("artifact://")
            if ref in artifact_id_set and ref not in refs:
                refs.append(ref)
        return tuple(refs) or artifact_ids

    def _fallback_all(
        self,
        dimensions: tuple[Dimension, ...],
        candidate_artifacts: tuple[Artifact, ...],
        reason: str,
    ) -> ProblemScoringResult:
        return ProblemScoringResult(
            signals=tuple(
                self._heuristic_signal(dimension, candidate_artifacts)
                for dimension in dimensions
            ),
            failures=tuple(
                ScorerFailed(dimension=dimension, reason=reason)
                for dimension in dimensions
            ),
        )

    def _heuristic_signal(
        self,
        dimension: Dimension,
        candidate_artifacts: tuple[Artifact, ...],
    ) -> Signal:
        text = "\n".join(artifact.body for artifact in candidate_artifacts).lower()
        keywords = self._DIMENSION_KEYWORDS[dimension]
        hits = sum(1 for keyword in keywords if keyword in text)
        value = min(1.0, max(0.0, 0.35 + (0.1 * hits)))
        source_refs = tuple(artifact.id for artifact in candidate_artifacts)
        return Signal(
            id=f"sig-problem-{dimension.value}-heuristic",
            dimension=dimension,
            value=value,
            confidence=0.2,
            source_refs=source_refs,
            emitted_by="problem_scorer:heuristic",
            justification=(
                f"Heuristic fallback for {dimension.value}: matched {hits} "
                f"of {len(keywords)} configured keywords."
            ),
            at=datetime.now(UTC),
        )

    @staticmethod
    def _source_suffix(source_refs: tuple[str, ...]) -> str:
        if not source_refs:
            return "no-artifacts"
        return "-".join(source_refs)

    @staticmethod
    def _reason(exc: Exception) -> str:
        return str(exc) or exc.__class__.__name__
