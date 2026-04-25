from __future__ import annotations

from adapters.scorer._base import BaseLlmScorer
from core.domain import Dimension


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
