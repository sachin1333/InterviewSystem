from __future__ import annotations

from adapters.scorer._base import BaseLlmScorer
from core.domain import Dimension


class LlmProblemFramingScorer(BaseLlmScorer):
    dimension = Dimension.problem_framing
    scorer_name = "scorer.problem_framing"
    template_dir_name = "problem_framing"
    heuristic_keywords = (
        "problem",
        "goal",
        "objective",
        "metric",
        "constraint",
        "define",
        "frame",
        "scope",
        "business",
    )
    heuristic_hit_value = 0.5
    heuristic_miss_value = 0.2
