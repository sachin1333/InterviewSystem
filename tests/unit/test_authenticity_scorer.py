from __future__ import annotations

from adapters.scorer.authenticity_scorer import AuthenticityScorer
from core.domain import Dimension
from core.events import LatencyObserved, SpeechFinalized


def test_low_latency_variance_reduces_authenticity() -> None:
    latencies = [
        LatencyObserved(
            turn_id=f"t{i}",
            stt_first_partial_ms=300,
            stt_final_ms=500,
            llm_ttft_ms=0,
            tts_first_byte_ms=0,
            end_to_end_ms=3000,
        )
        for i in range(5)
    ]
    speeches = [
        SpeechFinalized(
            turn_id=f"t{i}",
            transcript="generic answer",
            wpm=160,
            first_partial_ms=300,
            final_ms=2700,
            filler_count=0,
        )
        for i in range(5)
    ]

    signal = AuthenticityScorer().score(
        latencies,
        speeches,
        counterfactual_handled=False,
    )

    assert signal.dimension is Dimension.response_authenticity
    assert signal.value < 0.4
    assert signal.confidence > 0.6


def test_resume_specifics_help_authenticity() -> None:
    latencies = [
        LatencyObserved(
            turn_id=f"t{i}",
            stt_first_partial_ms=250 + (i * 40),
            stt_final_ms=450 + (i * 30),
            llm_ttft_ms=0,
            tts_first_byte_ms=0,
            end_to_end_ms=2200 + (i * 500),
        )
        for i in range(4)
    ]
    speeches = [
        SpeechFinalized(
            turn_id=f"t{i}",
            transcript="We handled 1.2M rows with a 7% positive class and retrained weekly.",
            wpm=145,
            first_partial_ms=200,
            final_ms=3000,
            filler_count=1,
        )
        for i in range(4)
    ]

    signal = AuthenticityScorer().score(
        latencies,
        speeches,
        counterfactual_handled=True,
        resume_specificity_score=1.0,
    )

    assert signal.value > 0.7
