from __future__ import annotations

from pathlib import Path

import pytest

from adapters.llm.factory import get_model_router
from adapters.llm.fake_router import FakeRouter
from adapters.llm.openai_router import OpenAIRouter


def test_factory_uses_openai_single_model_even_when_openrouter_key_exists() -> None:
    router = get_model_router(
        env={
            "OPENAI_API_KEY": "openai-key",
            "OPENROUTER_API_KEY": "openrouter-key",
            "OPENAI_MODEL": "gpt-custom",
            "OPENAI_REASONING_EFFORT": "low",
        }
    )

    assert isinstance(router.provider, OpenAIRouter)
    provider = router.provider
    assert provider.api_key == "openai-key"
    assert provider.model == "gpt-custom"
    assert provider.reasoning_effort == "low"


def test_factory_loads_openai_key_from_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "OPENAI_API_KEY=dotenv-openai-key\n"
        "OPENAI_MODEL=gpt-dotenv\n"
        "OPENAI_REASONING_EFFORT=low\n"
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)

    router = get_model_router(dotenv_path=dotenv_path)

    assert isinstance(router.provider, OpenAIRouter)
    provider = router.provider
    assert provider.api_key == "dotenv-openai-key"
    assert provider.model == "gpt-dotenv"


def test_factory_requires_openai_key_unless_fake_llm_enabled() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_model_router(env={})


def test_factory_preserves_fake_llm_for_offline_tests() -> None:
    router = get_model_router(env={"FAKE_LLM": "1"})

    assert isinstance(router.provider, FakeRouter)
