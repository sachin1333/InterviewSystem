from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, Rubric, Signal


def _signal(
    dimension: Dimension,
    value: float,
    confidence: float,
    source_ref: str = "artifact://a1",
) -> Signal:
    return Signal(
        dimension=dimension,
        value=value,
        confidence=confidence,
        source_refs=(source_ref,),
        emitted_by="test-scorer",
        at=datetime(2026, 4, 22, tzinfo=UTC),
    )


def test_aggregator_computes_weighted_mean(tmp_path: Path) -> None:
    rubric = Rubric(
        version="demo@1",
        weights={
            Dimension.model_rationale: 0.6,
            Dimension.communication: 0.4,
        },
    )
    aggregator = RubricAggregator(rubric, output_dir=tmp_path)

    result = aggregator.aggregate(
        session_id="sess-1",
        signals=[
            _signal(Dimension.model_rationale, 0.8, 1.0),
            _signal(Dimension.communication, 0.5, 1.0, "artifact://a2"),
        ],
    )

    assert result.score.per_dimension == {
        Dimension.model_rationale: 0.8,
        Dimension.communication: 0.5,
    }
    assert result.score.composite == 0.68
    assert result.insufficient_dimensions == ()
    assert result.feedback_path.exists()


def test_aggregator_excludes_dimensions_with_insufficient_evidence(tmp_path: Path) -> None:
    rubric = Rubric(
        version="demo@1",
        weights={
            Dimension.model_rationale: 0.5,
            Dimension.communication: 0.5,
        },
    )
    aggregator = RubricAggregator(
        rubric,
        min_signals_per_dimension=1,
        output_dir=tmp_path,
    )

    result = aggregator.aggregate(
        session_id="sess-2",
        signals=[_signal(Dimension.communication, 0.6, 1.0)],
    )

    assert result.score.per_dimension == {Dimension.communication: 0.6}
    assert result.score.composite == 0.6
    assert result.insufficient_dimensions == (Dimension.model_rationale,)
    assert "insufficient evidence" in result.feedback_markdown.lower()


def test_aggregator_confidence_weights_signal_means(tmp_path: Path) -> None:
    rubric = Rubric(
        version="demo@1",
        weights={Dimension.model_rationale: 1.0},
    )
    aggregator = RubricAggregator(rubric, output_dir=tmp_path, confidence_weighting=True)

    result = aggregator.aggregate(
        session_id="sess-3",
        signals=[
            _signal(Dimension.model_rationale, 1.0, 0.9),
            _signal(Dimension.model_rationale, 0.0, 0.1, "artifact://a2"),
        ],
    )

    assert result.score.per_dimension[Dimension.model_rationale] == 0.9
    assert result.score.composite == 0.9
