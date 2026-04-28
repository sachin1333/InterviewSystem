from pathlib import Path

import pytest

from core.case_bank import CaseBank, CaseBankEmpty


def test_case_bank_loads_from_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bank.yaml"
    p.write_text(
        "version: '1'\n"
        "prompts:\n"
        "  - id: rev_lift\n"
        "    primitive: think_aloud\n"
        "    body: 'Marketing wants to predict revenue lift.'\n"
        "  - id: churn_v1\n"
        "    primitive: think_aloud\n"
        "    body: 'A subscription product is seeing churn climb 3pp.'\n"
    )
    bank = CaseBank.from_yaml(p)
    assert len(bank) == 2
    p1 = bank.pick_for_session("sess-aaaa")
    p2 = bank.pick_for_session("sess-aaaa")
    assert p1.id == p2.id  # deterministic per session


def test_case_bank_empty_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("version: '1'\nprompts: []\n")
    bank = CaseBank.from_yaml(p)
    with pytest.raises(CaseBankEmpty):
        bank.pick_for_session("sess-x")


def test_case_bank_distributes_across_sessions(tmp_path: Path) -> None:
    p = tmp_path / "bank.yaml"
    p.write_text(
        "version: '1'\n"
        "prompts:\n"
        "  - {id: a, primitive: think_aloud, body: 'A'}\n"
        "  - {id: b, primitive: think_aloud, body: 'B'}\n"
        "  - {id: c, primitive: think_aloud, body: 'C'}\n"
        "  - {id: d, primitive: think_aloud, body: 'D'}\n"
    )
    bank = CaseBank.from_yaml(p)
    picks = {bank.pick_for_session(f"sess-{i:04x}").id for i in range(64)}
    assert len(picks) >= 3  # uses ≥3 of 4 prompts across 64 hashes
