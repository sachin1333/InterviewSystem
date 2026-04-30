from __future__ import annotations

from datetime import UTC, datetime

from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, Rubric, Signal


def test_signal_carries_non_empty_justification_for_scorer_output() -> None:
    sig = Signal(
        id="sig-1",
        dimension=Dimension.model_rationale,
        value=0.8,
        confidence=0.9,
        source_refs=("artifact-1",),
        emitted_by="rationale_scorer",
        justification="Candidate compared nonlinear model against a simpler baseline.",
        at=datetime.now(UTC),
    )

    assert sig.justification.startswith("Candidate compared")


def test_aggregator_marks_missing_dimension_as_partial() -> None:
    rubric = Rubric(version="v1", weights={
        Dimension.model_rationale: 0.5,
        Dimension.communication: 0.5,
    })
    result = RubricAggregator(rubric).aggregate("sess-1", [
        Signal(
            id="sig-1",
            dimension=Dimension.model_rationale,
            value=0.8,
            confidence=0.9,
            source_refs=("artifact-1",),
            emitted_by="rationale_scorer",
            justification="Clear rationale.",
            at=datetime.now(UTC),
        )
    ])

    assert Dimension.communication in result.insufficient_dimensions
    assert "insufficient evidence" in result.feedback_markdown
