from __future__ import annotations

from adapters.scorer._base import BaseLlmScorer
from core.domain import Dimension


class LlmInsightInterpScorer(BaseLlmScorer):
    dimension = Dimension.insight_interp
    scorer_name = "scorer.insight_interp"
    template_dir_name = "insight_interp"
    heuristic_keywords = (
        "insight",
        "finding",
        "result",
        "pattern",
        "trend",
        "conclude",
        "interpret",
        "suggests",
        "shows",
    )
    heuristic_hit_value = 0.5
    heuristic_miss_value = 0.2
