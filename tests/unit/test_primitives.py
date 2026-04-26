from core.domain import Dimension
from core.primitives import PRIMITIVE_REGISTRY, Primitive


def test_all_primitives_have_specs() -> None:
    assert set(PRIMITIVE_REGISTRY.keys()) == set(Primitive)


def test_primitives_with_cheating_defense_flagged() -> None:
    cheat_defense = {p for p, s in PRIMITIVE_REGISTRY.items() if s.cheating_defense}
    assert cheat_defense == {
        Primitive.socratic_rebuttal,
        Primitive.counterfactual,
        Primitive.resume_deep_dive,
    }


def test_one_bullet_is_shortest() -> None:
    durations = {p: s.expected_seconds_max for p, s in PRIMITIVE_REGISTRY.items()}
    assert durations[Primitive.one_bullet] == min(durations.values())


def test_every_spec_has_at_least_one_primary_signal() -> None:
    for p, spec in PRIMITIVE_REGISTRY.items():
        assert spec.primary_signals, f"{p} has no primary signals"
        for s in spec.primary_signals:
            assert isinstance(s, Dimension)
