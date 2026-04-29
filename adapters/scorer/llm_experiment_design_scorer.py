from __future__ import annotations

from adapters.scorer._base import BaseLlmScorer
from core.domain import Dimension


class LlmExperimentDesignScorer(BaseLlmScorer):
    dimension = Dimension.experiment_design
    scorer_name = "scorer.experiment_design"
    template_dir_name = "experiment_design"
    heuristic_keywords = (
        "experiment",
        "validation",
        "holdout",
        "test",
        "guardrail",
        "power",
        "sample",
        "metric",
    )
    heuristic_hit_value = 0.5
    heuristic_miss_value = 0.2
