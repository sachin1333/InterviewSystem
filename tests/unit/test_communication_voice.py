from __future__ import annotations

from adapters.llm.router import ModelRouter
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from core.domain import Dimension


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


def test_communication_penalizes_high_filler_rate() -> None:
    router = ModelRouter(
        _StaticProvider(
            '{"signals":[{"dimension":"communication","value":0.8,"confidence":0.9}]}'
        ),
        sleep=lambda _: None,
    )
    scorer = LlmCommunicationScorer(router)

    signal = scorer.score_voice(
        "um so like uh I think um the model um could be",
        filler_count=4,
        wpm=110,
        artifact_id="voice-artifact",
    )

    assert signal.dimension is Dimension.communication
    assert signal.value < 0.5


def test_communication_voice_uses_artifact_source_ref() -> None:
    router = ModelRouter(
        _StaticProvider(
            '{"signals":[{"dimension":"communication","value":0.7,"confidence":0.9}]}'
        ),
        sleep=lambda _: None,
    )
    scorer = LlmCommunicationScorer(router)

    signal = scorer.score_voice(
        "Concise, stakeholder-friendly explanation.",
        filler_count=0,
        wpm=145,
        artifact_id="voice-artifact-2",
    )

    assert signal.source_refs == ("voice-artifact-2",)
