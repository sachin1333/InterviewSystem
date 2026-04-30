from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from core.domain import Dimension, Rubric, Score, Signal
from core.rubric_loader import load_rubric


@dataclass(frozen=True)
class AggregateResult:
    score: Score
    insufficient_dimensions: tuple[Dimension, ...]
    feedback_path: Path
    feedback_markdown: str


class RubricAggregator:
    def __init__(
        self,
        rubric: Rubric,
        *,
        min_signals_per_dimension: int = 1,
        confidence_weighting: bool = True,
        output_dir: str | Path = "outputs",
    ) -> None:
        self.rubric = rubric
        self.min_signals_per_dimension = min_signals_per_dimension
        self.confidence_weighting = confidence_weighting
        self.output_dir = Path(output_dir)

    @classmethod
    def from_yaml(
        cls,
        yaml_path: str | Path,
        *,
        output_dir: str | Path = "outputs",
    ) -> RubricAggregator:
        path = Path(yaml_path)
        data = yaml.safe_load(path.read_text(encoding="utf8")) or {}
        aggregation = data.get("aggregation") or {}
        return cls(
            load_rubric(yaml_path=path),
            min_signals_per_dimension=int(
                aggregation.get("min_signals_per_dimension", 1)
            ),
            confidence_weighting=bool(
                aggregation.get("confidence_weighting", True)
            ),
            output_dir=output_dir,
        )

    def aggregate(
        self,
        session_id: str,
        signals: Sequence[Signal] | Iterable[Signal],
        *,
        at: datetime | None = None,
    ) -> AggregateResult:
        grouped: dict[Dimension, list[Signal]] = defaultdict(list)
        for signal in signals:
            if signal.dimension in self.rubric.weights:
                grouped[signal.dimension].append(signal)

        per_dimension: dict[Dimension, float] = {}
        insufficient_dimensions: list[Dimension] = []
        weighted_total = 0.0
        included_weight = 0.0

        for dimension, weight in self.rubric.weights.items():
            dimension_signals = grouped.get(dimension, [])
            if len(dimension_signals) < self.min_signals_per_dimension:
                insufficient_dimensions.append(dimension)
                continue

            reviewer_signals = [
                signal for signal in dimension_signals
                if signal.emitted_by.startswith("reviewer:")
            ]
            dimension_score = self._dimension_score(reviewer_signals or dimension_signals)
            if dimension_score is None:
                insufficient_dimensions.append(dimension)
                continue

            rounded = round(dimension_score, 4)
            per_dimension[dimension] = rounded
            weighted_total += rounded * weight
            included_weight += weight

        composite = round(weighted_total / included_weight, 4) if included_weight else 0.0
        score = Score(
            session_id=session_id,
            rubric_version=self.rubric.version,
            per_dimension=per_dimension,
            composite=composite,
            at=at or datetime.now(UTC),
        )
        feedback_markdown = self._feedback_markdown(
            session_id=session_id,
            per_dimension=per_dimension,
            insufficient_dimensions=tuple(insufficient_dimensions),
            composite=composite,
        )
        feedback_path = self.output_dir / f"{session_id}_feedback.md"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        feedback_path.write_text(feedback_markdown, encoding="utf8")
        return AggregateResult(
            score=score,
            insufficient_dimensions=tuple(insufficient_dimensions),
            feedback_path=feedback_path,
            feedback_markdown=feedback_markdown,
        )

    def _dimension_score(self, signals: Sequence[Signal]) -> float | None:
        if not self.confidence_weighting:
            return sum(signal.value for signal in signals) / len(signals)

        total_confidence = sum(signal.confidence for signal in signals)
        if total_confidence <= 0:
            return None
        return sum(signal.value * signal.confidence for signal in signals) / total_confidence

    def _feedback_markdown(
        self,
        *,
        session_id: str,
        per_dimension: dict[Dimension, float],
        insufficient_dimensions: tuple[Dimension, ...],
        composite: float,
    ) -> str:
        dimension_lines = [
            f"- {dimension.value}: {value:.2f}"
            for dimension, value in per_dimension.items()
        ]
        if not dimension_lines:
            dimension_lines = ["- no scored dimensions yet"]

        insufficient_lines = [
            f"- {dimension.value}: insufficient evidence"
            for dimension in insufficient_dimensions
        ]
        if not insufficient_lines:
            insufficient_lines = ["- none"]

        return "\n".join(
            [
                "# Candidate Feedback",
                "",
                f"- Session ID: {session_id}",
                f"- Rubric: {self.rubric.version}",
                "",
                "## Per-dimension signals",
                *dimension_lines,
                "",
                "## Composite score",
                f"- Composite (0.0 - 1.0): {composite:.2f}",
                "",
                "## Insufficient evidence",
                *insufficient_lines,
            ]
        )
