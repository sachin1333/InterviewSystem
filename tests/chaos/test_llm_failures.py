import pytest

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter


@pytest.mark.parametrize(
    ("mode", "expect_fallback"),
    [
        (None, False),
        ("timeout", True),
        ("5xx", True),
        ("malformed", True),
        ("rate_limit", True),
        ("empty", True),
    ],
)
def test_llm_failure_modes_return_fallback_or_prompt(
    mode: str | None,
    expect_fallback: bool,
) -> None:
    provider = FakeRouter(fail_mode=mode)
    router = ModelRouter(provider)
    challenger = LlmChallenger(router)

    prompts = list(challenger.propose_prompts("session-xyz"))
    assert len(prompts) == 1
    prompt = prompts[0]

    if expect_fallback:
        assert "Generated prompt" not in prompt
    else:
        assert "Generated prompt" in prompt

    assert provider.calls, "FakeRouter should have recorded at least one call"
