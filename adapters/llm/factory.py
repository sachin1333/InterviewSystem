from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from adapters.llm.fake_router import FakeRouter
from adapters.llm.openai_router import OpenAIRouter
from adapters.llm.router import ModelRouter, RouterProvider
from core.observability import GLOBAL_METRICS

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_model_router(
    env: Mapping[str, str] | None = None,
    *,
    dotenv_path: str | Path | None = None,
) -> ModelRouter:
    """Return the configured LLM wrapper.

    Production uses one OpenAI model.  ``FAKE_LLM=1`` remains available for
    deterministic offline tests and demos.
    """

    environment = _environment_with_dotenv(dotenv_path) if env is None else env
    provider: RouterProvider
    if environment.get("FAKE_LLM"):
        provider = FakeRouter()
    else:
        api_key = environment.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required; set it in .env or the shell")
        provider = OpenAIRouter(
            api_key=api_key,
            model=environment.get("OPENAI_MODEL"),
            reasoning_effort=environment.get("OPENAI_REASONING_EFFORT"),
        )

    return ModelRouter(provider, metrics=GLOBAL_METRICS)


def _environment_with_dotenv(dotenv_path: str | Path | None) -> Mapping[str, str]:
    path = Path(dotenv_path) if dotenv_path is not None else _PROJECT_ROOT / ".env"
    try:
        from dotenv import dotenv_values
    except Exception:  # pragma: no cover - dependency/environment dependent
        return os.environ

    dotenv_environment = {
        key: value
        for key, value in dotenv_values(path).items()
        if value is not None
    }
    # Match python-dotenv override=False semantics: shell env wins over .env.
    return {**dotenv_environment, **os.environ}
