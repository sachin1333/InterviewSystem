from __future__ import annotations

from adapters.scorer.authenticity_scorer import AuthenticityScorer
from core.events import LatencyObserved, SpeechFinalized


def test_ai_coached_candidate_scores_low_authenticity() -> None:
    latencies = [
        LatencyObserved(
            turn_id=f"t{i}",
            stt_first_partial_ms=300,
            stt_final_ms=450,
            llm_ttft_ms=0,
            tts_first_byte_ms=0,
            end_to_end_ms=3000,
        )
        for i in range(6)
    ]
    speeches = [
        SpeechFinalized(
            turn_id=f"t{i}",
            transcript="I would align to the business goal and choose the right model.",
            wpm=165,
            first_partial_ms=300,
            final_ms=2600,
            filler_count=0,
        )
        for i in range(6)
    ]

    signal = AuthenticityScorer().score(
        latencies,
        speeches,
        counterfactual_handled=False,
        generic_counterfactual_response=True,
        resume_specificity_score=0.2,
    )

    assert signal.value < 0.5
    assert signal.confidence >= 0.7
