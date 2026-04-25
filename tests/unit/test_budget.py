from adapters.llm.budget import SessionBudget


def test_debit_below_cap_not_exceeded() -> None:
    b = SessionBudget(session_id="s1", cap_usd=0.40)
    result = b.debit(tokens_in=100, tokens_out=50,
                     price_in_per_1m=0.60, price_out_per_1m=2.50)
    assert not result.exceeded
    assert result.cost_usd > 0


def test_debit_exceeds_cap() -> None:
    b = SessionBudget(session_id="s2", cap_usd=0.00001)
    result = b.debit(tokens_in=10000, tokens_out=10000,
                     price_in_per_1m=0.60, price_out_per_1m=2.50)
    assert result.exceeded
    assert result.over_by_usd > 0


def test_total_accumulates() -> None:
    b = SessionBudget(session_id="s3", cap_usd=1.0)
    b.debit(tokens_in=100, tokens_out=100,
            price_in_per_1m=1.0, price_out_per_1m=1.0)
    b.debit(tokens_in=100, tokens_out=100,
            price_in_per_1m=1.0, price_out_per_1m=1.0)
    assert b.total_usd > 0
