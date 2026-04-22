def test_contracts_importable():
    import core.contracts as contracts

    assert hasattr(contracts, "Challenger")
    assert hasattr(contracts, "Examiner")
    assert hasattr(contracts, "Scorer")
    assert hasattr(contracts, "Runtime")
    assert hasattr(contracts, "UIAdapter")
