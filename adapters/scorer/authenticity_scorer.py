from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import sqrt

from core.domain import Dimension, Signal
from core.events import LatencyObserved, SpeechFinalized


@dataclass(frozen=True)
class AuthenticityBreakdown:
    latency_consistency: float
    speech_texture: float
    counterfactual_adaptation: float
    resume_specificity: float


class AuthenticityScorer:
    """Heuristic scorer for voice-turn response authenticity.

    The scorer intentionally stays local/deterministic:
    - suspiciously constant end-to-end latency lowers the score
    - some natural speech texture helps; uniformly polished zero-filler turns do not
    - counterfactual adaptation and resume specificity raise/lower trust materially
    """

    dimension = Dimension.response_authenticity
    scorer_name = "scorer.response_authenticity"

    def score(
        self,
        latencies: Sequence[LatencyObserved],
        speeches: Sequence[SpeechFinalized],
        *,
        counterfactual_handled: bool,
        generic_counterfactual_response: bool = False,
        resume_specificity_score: float = 0.8,
        source_refs: tuple[str, ...] = (),
    ) -> Signal:
        breakdown = self._breakdown(
            latencies,
            speeches,
            counterfactual_handled=counterfactual_handled,
            generic_counterfactual_response=generic_counterfactual_response,
            resume_specificity_score=resume_specificity_score,
        )
        value = (
            (0.45 * breakdown.latency_consistency)
            + (0.15 * breakdown.speech_texture)
            + (0.25 * breakdown.counterfactual_adaptation)
            + (0.15 * breakdown.resume_specificity)
        )
        confidence = self._confidence(latencies, speeches, counterfactual_seen=True)
        refs = source_refs or tuple(lat.turn_id for lat in latencies[-3:]) or ("voice://authenticity",)
        return Signal(
            dimension=self.dimension,
            value=round(_clamp(value), 4),
            confidence=round(_clamp(confidence), 4),
            source_refs=refs,
            emitted_by=self.scorer_name,
            at=datetime.now(UTC),
        )

    def _breakdown(
        self,
        latencies: Sequence[LatencyObserved],
        speeches: Sequence[SpeechFinalized],
        *,
        counterfactual_handled: bool,
        generic_counterfactual_response: bool,
        resume_specificity_score: float,
    ) -> AuthenticityBreakdown:
        return AuthenticityBreakdown(
            latency_consistency=self._latency_consistency(latencies),
            speech_texture=self._speech_texture(speeches),
            counterfactual_adaptation=self._counterfactual_score(
                counterfactual_handled=counterfactual_handled,
                generic_counterfactual_response=generic_counterfactual_response,
            ),
            resume_specificity=_clamp(resume_specificity_score),
        )

    def _latency_consistency(self, latencies: Sequence[LatencyObserved]) -> float:
        if len(latencies) < 2:
            return 0.6
        values = [float(item.end_to_end_ms) for item in latencies if item.end_to_end_ms > 0]
        if len(values) < 2:
            return 0.6
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        stddev = sqrt(variance)
        coeff = stddev / mean if mean > 0 else 0.0
        if coeff < 0.03:
            return 0.1
        if coeff < 0.08:
            return 0.25
        if coeff < 0.15:
            return 0.55
        return 0.85

    def _speech_texture(self, speeches: Sequence[SpeechFinalized]) -> float:
        if not speeches:
            return 0.6
        words = sum(max(len(item.transcript.split()), 1) for item in speeches)
        fillers = sum(item.filler_count for item in speeches)
        filler_rate = fillers / words
        avg_wpm = sum(item.wpm for item in speeches) / len(speeches)

        score = 0.8
        if filler_rate == 0:
            score -= 0.25
        elif filler_rate > 0.18:
            score -= 0.2
        elif filler_rate > 0.08:
            score -= 0.1

        if avg_wpm < 85 or avg_wpm > 190:
            score -= 0.15
        return _clamp(score)

    @staticmethod
    def _counterfactual_score(
        *,
        counterfactual_handled: bool,
        generic_counterfactual_response: bool,
    ) -> float:
        if generic_counterfactual_response:
            return 0.15
        if counterfactual_handled:
            return 0.9
        return 0.2

    @staticmethod
    def _confidence(
        latencies: Sequence[LatencyObserved],
        speeches: Sequence[SpeechFinalized],
        *,
        counterfactual_seen: bool,
    ) -> float:
        evidence_points = min(len(latencies), 6) + min(len(speeches), 6)
        confidence = 0.25 + (evidence_points * 0.05)
        if counterfactual_seen:
            confidence += 0.15
        return min(confidence, 0.95)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
