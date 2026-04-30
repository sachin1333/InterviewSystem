from __future__ import annotations

from datetime import UTC, datetime

from adapters.scorer.aggregator import RubricAggregator
from core.domain import Dimension, Rubric, Signal


def test_reviewer_signal_overrides_scorer_signal_for_same_dimension() -> None:
    rubric = Rubric(version="v1", weights={Dimension.communication: 1.0})
    scorer_signal = Signal(
        id="sig-scorer",
        dimension=Dimension.communication,
        value=0.4,
        confidence=0.8,
        source_refs=("turn-1",),
        emitted_by="communication_scorer",
        justification="Scorer saw unclear explanation.",
        at=datetime.now(UTC),
    )
    reviewer_signal = Signal(
        id="sig-reviewer",
        dimension=Dimension.communication,
        value=0.9,
        confidence=1.0,
        source_refs=("turn-1",),
        emitted_by="reviewer:recruiter-1",
        justification="Human reviewed transcript and found explanation strong.",
        at=datetime.now(UTC),
    )

    result = RubricAggregator(rubric).aggregate("sess-1", [scorer_signal, reviewer_signal])

    assert result.score.per_dimension[Dimension.communication] == 0.9
