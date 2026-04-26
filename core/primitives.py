from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.domain import Dimension


class Primitive(StrEnum):
    think_aloud = "think_aloud"
    socratic_rebuttal = "socratic_rebuttal"
    counterfactual = "counterfactual"
    resume_deep_dive = "resume_deep_dive"
    verbal_whiteboard = "verbal_whiteboard"
    one_bullet = "one_bullet"


@dataclass(frozen=True)
class PrimitiveSpec:
    primitive: Primitive
    expected_seconds_min: int
    expected_seconds_max: int
    primary_signals: tuple[Dimension, ...]
    description: str
    cheating_defense: bool


PRIMITIVE_REGISTRY: dict[Primitive, PrimitiveSpec] = {
    Primitive.think_aloud: PrimitiveSpec(
        primitive=Primitive.think_aloud,
        expected_seconds_min=90, expected_seconds_max=120,
        primary_signals=(Dimension.problem_framing, Dimension.insight_interp),
        description="Force candidate to externalize first-five-steps reasoning out loud.",
        cheating_defense=False,
    ),
    Primitive.socratic_rebuttal: PrimitiveSpec(
        primitive=Primitive.socratic_rebuttal,
        expected_seconds_min=60, expected_seconds_max=90,
        primary_signals=(Dimension.model_rationale, Dimension.communication),
        description="Challenge a stated method choice; require defense or pivot.",
        cheating_defense=True,
    ),
    Primitive.counterfactual: PrimitiveSpec(
        primitive=Primitive.counterfactual,
        expected_seconds_min=45, expected_seconds_max=90,
        primary_signals=(Dimension.model_rationale, Dimension.problem_framing),
        description="Inject 'assume X changed' to disrupt cached reasoning.",
        cheating_defense=True,
    ),
    Primitive.resume_deep_dive: PrimitiveSpec(
        primitive=Primitive.resume_deep_dive,
        expected_seconds_min=60, expected_seconds_max=120,
        primary_signals=(Dimension.problem_framing,),
        description="Ask for class imbalance ratios / dataset shapes from a stated prior project.",
        cheating_defense=True,
    ),
    Primitive.verbal_whiteboard: PrimitiveSpec(
        primitive=Primitive.verbal_whiteboard,
        expected_seconds_min=120, expected_seconds_max=180,
        primary_signals=(Dimension.experiment_design, Dimension.communication),
        description="Describe a multi-stage pipeline (e.g., retraining loop) in words only.",
        cheating_defense=False,
    ),
    Primitive.one_bullet: PrimitiveSpec(
        primitive=Primitive.one_bullet,
        expected_seconds_min=15, expected_seconds_max=30,
        primary_signals=(Dimension.communication,),
        description="One sentence: why does this metric matter?",
        cheating_defense=False,
    ),
}
