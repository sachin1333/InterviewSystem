from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.dispatcher import dispatch_scorers
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from core.domain import Artifact, ArtifactKind, Dimension, Rubric


class _StaticProvider:
    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        return (
            '{"signals":[{"dimension":"communication","value":0.8,"confidence":0.9,'
            '"source_refs":["artifact://artifact-1"],"note":"Clear recommendation"}]}'
        )


def test_partial_score_survives_when_one_scorer_is_missing(tmp_path) -> None:
    artifact = Artifact(
        id="artifact-1",
        kind=ArtifactKind.markdown,
        version=1,
        body="Recommendation: launch the baseline model and monitor false positives weekly.",
        produced_by_turn_id="turn-1",
        at=datetime(2026, 4, 22, tzinfo=UTC),
    )
    scorer = LlmCommunicationScorer(ModelRouter(_StaticProvider(), sleep=lambda _: None))

    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = dispatch_scorers("sess-1", artifact, [scorer], executor=executor)
        results = [future.result() for future in futures]

    signals = [result.signal for result in results if result.signal is not None]
    rubric = Rubric(
        version="delta@1",
        weights={
            Dimension.communication: 0.5,
            Dimension.model_rationale: 0.5,
        },
    )
    aggregate = RubricAggregator(rubric, output_dir=tmp_path).aggregate("sess-1", signals)

    assert aggregate.score.composite == 0.8
    assert aggregate.insufficient_dimensions == (Dimension.model_rationale,)
    assert aggregate.feedback_path.exists()
