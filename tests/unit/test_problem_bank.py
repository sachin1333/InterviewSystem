from __future__ import annotations

from pathlib import Path

import pytest

from core.domain import Dimension, ProblemId
from core.problem_bank import ProblemBank, ProblemBankError


def _write_bank(path: Path) -> None:
    path.write_text(
        "version: '1'\n"
        "problems:\n"
        "  - id: framing_churn\n"
        "    opener: 'How would you frame the churn problem?'\n"
        "    context: 'Enterprise churn is rising after onboarding changes.'\n"
        "    target_dimensions: [problem_framing, communication]\n"
        "    dim_thresholds: {problem_framing: 0.70, communication: 0.60}\n"
        "    expected_duration_s: 420\n"
        "    tags: [framing, data-cleaning]\n"
        "  - id: model_choice\n"
        "    opener: 'Which model family would you try first and why?'\n"
        "    context: 'A tabular propensity model must ship in two weeks.'\n"
        "    target_dimensions: [model_rationale, experiment_design]\n"
        "    dim_thresholds: {model_rationale: 0.75, experiment_design: 0.60}\n"
        "    expected_duration_s: 360\n"
        "    tags: [modeling]\n"
        "  - id: ab_eval\n"
        "    opener: 'How would you interpret this A/B test?'\n"
        "    context: 'CTR rose, revenue stayed flat, PM wants to ship.'\n"
        "    target_dimensions: [experiment_design, insight_interp]\n"
        "    dim_thresholds: {experiment_design: 0.70, insight_interp: 0.70}\n"
        "    expected_duration_s: 360\n"
        "    tags: [experimentation]\n"
        "  - id: stakeholder_story\n"
        "    opener: 'How would you explain model drift to the PM?'\n"
        "    context: 'Approval rate dropped while AUC stayed flat.'\n"
        "    target_dimensions: [communication, insight_interp]\n"
        "    dim_thresholds: {communication: 0.75, insight_interp: 0.65}\n"
        "    expected_duration_s: 300\n"
        "    tags: [stakeholder]\n",
        encoding="utf-8",
    )


def test_problem_bank_loads_schema_into_problem_dataclasses(tmp_path: Path) -> None:
    path = tmp_path / "bank.yaml"
    _write_bank(path)

    bank = ProblemBank.from_yaml(path)

    assert len(bank) == 4
    first = bank.problems[0]
    assert first.id == ProblemId("framing_churn")
    assert first.opener_text == "How would you frame the churn problem?"
    assert first.context.startswith("Enterprise churn")
    assert first.target_dimensions == (Dimension.problem_framing, Dimension.communication)
    assert first.dim_thresholds[Dimension.problem_framing] == 0.70
    assert first.expected_duration_s == 420
    assert bank.tags_for(ProblemId("framing_churn")) == ("framing", "data-cleaning")


def test_problem_bank_validation_errors_are_actionable(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "version: '1'\n"
        "problems:\n"
        "  - id: bad\n"
        "    opener: ''\n"
        "    context: 'x'\n"
        "    target_dimensions: [not_a_dimension]\n"
        "    dim_thresholds: {not_a_dimension: 1.7}\n"
        "    expected_duration_s: -1\n"
        "    tags: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ProblemBankError) as exc:
        ProblemBank.from_yaml(path)

    message = str(exc.value)
    assert "problems[0].opener" in message
    assert "not_a_dimension" in message
    assert "expected_duration_s" in message


def test_pick_sequence_is_deterministic_balanced_and_uses_three_or_four(tmp_path: Path) -> None:
    path = tmp_path / "bank.yaml"
    _write_bank(path)
    bank = ProblemBank.from_yaml(path)

    seq1 = bank.pick_sequence("sess-123", count=3)
    seq2 = bank.pick_sequence("sess-123", count=3)

    assert [p.id for p in seq1] == [p.id for p in seq2]
    assert len(seq1) == 3
    assert len({p.id for p in seq1}) == 3
    covered_dims = {dim for p in seq1 for dim in p.target_dimensions}
    assert len(covered_dims) >= 4


def test_pick_sequence_count_defaults_to_four_when_available(tmp_path: Path) -> None:
    path = tmp_path / "bank.yaml"
    _write_bank(path)
    bank = ProblemBank.from_yaml(path)

    seq = bank.pick_sequence("sess-default")

    assert len(seq) == 4
    assert len({p.id for p in seq}) == 4


def test_pick_sequence_fairness_over_1000_sessions(tmp_path: Path) -> None:
    path = tmp_path / "bank.yaml"
    _write_bank(path)
    bank = ProblemBank.from_yaml(path)
    counts = {problem.id: 0 for problem in bank.problems}

    for i in range(1000):
        for problem in bank.pick_sequence(f"sess-{i:04d}", count=3):
            counts[problem.id] += 1

    # There are 3000 selections across 4 problems; ideal is 750 each. The stable
    # hash shuffle should keep every problem well within a broad 25% tolerance.
    assert all(560 <= count <= 940 for count in counts.values())


def test_prewarm_openers_returns_all_candidate_visible_openers(tmp_path: Path) -> None:
    path = tmp_path / "bank.yaml"
    _write_bank(path)
    bank = ProblemBank.from_yaml(path)

    openers = bank.prewarm_openers()

    assert openers == tuple(problem.opener_text for problem in bank.problems)
