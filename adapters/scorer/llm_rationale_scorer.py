from __future__ import annotations

from adapters.scorer._base import BaseLlmScorer
from core.domain import Dimension


class LlmRationaleScorer(BaseLlmScorer):
    dimension = Dimension.model_rationale
    scorer_name = "scorer.rationale"
    template_dir_name = "rationale"
    heuristic_keywords = (
        "because",
        "trade-off",
        "baseline",
        "model",
        "feature",
        "assumption",
        "compare",
    )
    heuristic_hit_value = 0.45
    heuristic_miss_value = 0.2
