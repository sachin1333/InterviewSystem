from __future__ import annotations

from core.domain import Dimension, Problem, ProblemId
from core.problem_bank import ProblemBank, ProblemBankEntry
from core.problem_selection import ProfileAwareProblemSelector, ProfileFeatures, tokenize_terms


def _entry(
    problem_id: str,
    *,
    opener: str,
    context: str,
    dimensions: tuple[Dimension, ...],
    tags: tuple[str, ...],
) -> ProblemBankEntry:
    return ProblemBankEntry(
        problem=Problem(
            id=ProblemId(problem_id),
            opener_text=opener,
            context=context,
            target_dimensions=dimensions,
            dim_thresholds={dimension: 0.7 for dimension in dimensions},
            expected_duration_s=300,
        ),
        tags=tags,
    )


def _entries() -> tuple[ProblemBankEntry, ...]:
    return (
        _entry(
            "stakeholder_story",
            opener="Explain model drift to a product stakeholder.",
            context="A PM needs a concise narrative about approval rate changes.",
            dimensions=(Dimension.communication,),
            tags=("stakeholder", "product"),
        ),
        _entry(
            "pricing_ab_test",
            opener="Interpret a pricing A/B test with mixed revenue results.",
            context="The experiment changed checkout prices and affected conversion.",
            dimensions=(Dimension.experiment_design, Dimension.insight_interp),
            tags=("pricing", "experimentation"),
        ),
        _entry(
            "recommender_model",
            opener="Choose a model family for product recommendations.",
            context="A recommender system must launch with limited labeled data.",
            dimensions=(Dimension.model_rationale,),
            tags=("recommendations", "modeling"),
        ),
        _entry(
            "pricing_forecast",
            opener="Frame a pricing forecast for next quarter.",
            context="Finance wants a pricing forecast after a packaging change.",
            dimensions=(Dimension.experiment_design,),
            tags=("pricing", "forecasting"),
        ),
        _entry(
            "fraud_framing",
            opener="Frame a fraud detection investigation.",
            context="Chargebacks rose after a traffic mix shift.",
            dimensions=(Dimension.problem_framing,),
            tags=("fraud", "risk"),
        ),
    )


def test_tokenizer_returns_lowercase_alphanumeric_terms() -> None:
    assert tokenize_terms("Pricing/A-B_test, v2.0!") == ("pricing", "a", "b", "test", "v2", "0")


def test_profile_skills_and_tags_rank_matching_problems_first() -> None:
    selector = ProfileAwareProblemSelector()
    profile = ProfileFeatures(
        role_text="Senior growth data scientist",
        skills=("Pricing", "Experimentation"),
        claims_text="Owned checkout pricing experiments and revenue analysis.",
    )

    selection = selector.select("session-1", profile, _entries(), count=3)

    assert [problem.id for problem in selection.problems][:2] == [
        ProblemId("pricing_ab_test"),
        ProblemId("pricing_forecast"),
    ]
    assert selection.selection_rationale["pricing_ab_test"] == (
        "experimentation",
        "pricing",
    )


def test_dimension_coverage_preserves_a_broad_interview() -> None:
    selector = ProfileAwareProblemSelector()
    profile = ProfileFeatures(
        role_text="Pricing experimentation lead",
        skills=("pricing", "experimentation", "forecasting"),
        claims_text="Repeated pricing test ownership.",
    )

    selection = selector.select("session-coverage", profile, _entries(), count=4)

    selected_ids = {problem.id for problem in selection.problems}
    assert {"pricing_ab_test", "pricing_forecast"} <= selected_ids
    covered = {dimension for problem in selection.problems for dimension in problem.target_dimensions}
    assert len(covered) >= 4


def test_same_inputs_produce_deterministic_ordering_and_rationale() -> None:
    selector = ProfileAwareProblemSelector()
    profile = ProfileFeatures(
        role_text="Product data scientist",
        skills=("recommendations", "modeling"),
        claims_text="Built recommender launch metrics.",
    )

    first = selector.select("same-session", profile, _entries(), count=4)
    second = selector.select("same-session", profile, _entries(), count=4)

    assert [problem.id for problem in first.problems] == [problem.id for problem in second.problems]
    assert first.selection_rationale == second.selection_rationale


def test_empty_profile_falls_back_to_problem_bank_pick_sequence() -> None:
    entries = _entries()
    selector = ProfileAwareProblemSelector()
    profile = ProfileFeatures(role_text="", skills=(), claims_text="")

    selection = selector.select("fallback-session", profile, entries, count=4)
    expected = ProblemBank(entries).pick_sequence("fallback-session", count=4)

    assert selection.problems == tuple(expected)
    assert selection.selection_rationale == {problem.id: () for problem in expected}
