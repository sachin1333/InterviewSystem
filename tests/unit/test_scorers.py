from __future__ import annotations

from datetime import UTC, datetime

from adapters.llm.router import ModelRouter
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from core.domain import Artifact, ArtifactKind, Dimension


class _StaticProvider:
    def __init__(self, response: str) -> None:
        self.response = response

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        return self.response


class _FailingProvider:
    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        raise RuntimeError("simulated scorer failure")


def _artifact(body: str) -> Artifact:
    return Artifact(
        id="artifact-1",
        kind=ArtifactKind.markdown,
        version=1,
        body=body,
        produced_by_turn_id="turn-1",
        at=datetime(2026, 4, 22, tzinfo=UTC),
    )


def test_rationale_scorer_parses_signal_from_llm_json() -> None:
    router = ModelRouter(
        _StaticProvider(
            '{"signals":[{"dimension":"model_rationale","value":0.7,"confidence":0.8,'
            '"source_refs":["artifact://artifact-1"],"note":"Clear trade-off analysis"}]}'
        ),
        sleep=lambda _: None,
    )
    scorer = LlmRationaleScorer(router)

    result = scorer.score_optional("sess-1", _artifact("I chose logistic regression because..."))

    assert result.signal is not None
    assert result.failure is None
    assert result.signal.dimension is Dimension.model_rationale
    assert result.signal.value == 0.7
    assert result.signal.confidence == 0.8


def test_communication_scorer_uses_low_confidence_fallback_on_failure() -> None:
    router = ModelRouter(_FailingProvider(), max_retries=1, sleep=lambda _: None)
    scorer = LlmCommunicationScorer(router)

    result = scorer.score_optional(
        "sess-2",
        _artifact("Recommendation: ship the baseline first because stakeholders need clarity."),
    )

    assert result.signal is not None
    assert result.signal.dimension is Dimension.communication
    assert result.signal.confidence == 0.2
    assert result.failure is not None
    assert result.failure.dimension is Dimension.communication
