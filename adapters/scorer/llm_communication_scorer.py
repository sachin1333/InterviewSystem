from __future__ import annotations

from datetime import UTC, datetime

from adapters.scorer._base import BaseLlmScorer
from core.domain import Artifact, ArtifactKind, Dimension, Signal


class LlmCommunicationScorer(BaseLlmScorer):
    dimension = Dimension.communication
    scorer_name = "scorer.communication"
    template_dir_name = "communication"
    heuristic_keywords = (
        "recommend",
        "stakeholder",
        "summary",
        "should",
        "decision",
        "impact",
        "risk",
    )
    heuristic_hit_value = 0.5
    heuristic_miss_value = 0.25

    def score_voice(
        self,
        transcript: str,
        filler_count: int,
        wpm: int,
        *,
        artifact_id: str,
        session_id: str = "voice-session",
    ) -> Signal:
        artifact = Artifact(
            id=artifact_id,
            kind=ArtifactKind.transcript,
            version=1,
            body=transcript,
            produced_by_turn_id="voice-turn",
            at=datetime.now(UTC),
        )
        base = self.score_optional(session_id, artifact).signal or self._heuristic_signal(artifact)

        word_count = max(len(transcript.split()), 1)
        filler_penalty = min((filler_count / word_count) * 2.0, 0.4)

        wpm_penalty = 0.0
        if wpm and (wpm < 90 or wpm > 185):
            wpm_penalty = 0.1

        return Signal(
            dimension=base.dimension,
            value=max(0.0, min(1.0, round(base.value - filler_penalty - wpm_penalty, 4))),
            confidence=base.confidence,
            source_refs=(artifact_id,),
            emitted_by=base.emitted_by,
            at=base.at,
        )
