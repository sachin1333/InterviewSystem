from __future__ import annotations

import os

from adapters.llm.fake_router import FakeRouter
from adapters.llm.openai_router import OpenAIRouter
from adapters.llm.router import ModelRouter, RouterProvider


def get_model_router(env: dict[str, str] | None = None) -> ModelRouter:
    """Return a configured ModelRouter based on environment."""

    environment = env or os.environ
    provider: RouterProvider
    if environment.get("FAKE_LLM"):
        provider = FakeRouter()
    elif environment.get("OPENROUTER_API_KEY"):
        from adapters.llm.openrouter_router import OpenRouterRouter
        provider = OpenRouterRouter(api_key=environment["OPENROUTER_API_KEY"])
    elif environment.get("OPENAI_API_KEY"):
        provider = OpenAIRouter(api_key=environment.get("OPENAI_API_KEY"))
    else:
        provider = FakeRouter()

    return ModelRouter(provider)
