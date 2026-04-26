from pathlib import Path

from core.case_loader import load_case
from core.primitives import Primitive

CASE_PATH = Path("templates/cases/multi_stage_case_v1.yaml")


def test_case_loads_with_five_stages() -> None:
    case = load_case(CASE_PATH)
    assert len(case.stages) == 5
    assert case.version == "1.0"


def test_case_stage_sequence_matches_expected_primitives() -> None:
    case = load_case(CASE_PATH)
    seq = case.stage_sequence()
    assert [s.id for s in seq] == [
        "problem_framing", "methodology", "execution", "interpretation", "synthesis",
    ]
    assert [s.primitive for s in seq] == [
        Primitive.think_aloud,
        Primitive.socratic_rebuttal,
        Primitive.verbal_whiteboard,
        Primitive.counterfactual,
        Primitive.one_bullet,
    ]


def test_total_duration_within_advertised_envelope() -> None:
    case = load_case(CASE_PATH)
    total_s = sum(s.duration_s for s in case.stage_sequence())
    # 120 + 75 + 150 + 60 + 25 = 430s = ~7.2 min; advertised 18min total leaves slack for probes
    assert 0 < total_s <= case.total_duration_min * 60
