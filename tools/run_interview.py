#!/usr/bin/env python3
"""Small demo bootstrap to exercise challengers with configurable LLM router.

Usage:
  FAKE_LLM=1 python tools/run_interview.py
  OPENAI_API_KEY=... python tools/run_interview.py

This script is intentionally small: it creates a `ModelRouter` via the
factory, instantiates `LlmChallenger`, and prints one proposed prompt.
"""

from __future__ import annotations

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.llm.factory import get_model_router


def main() -> None:
    router = get_model_router()
    challenger = LlmChallenger(router)
    prompts = list(challenger.propose_prompts("demo-session"))
    print("Proposed prompts:\n")
    for prompt in prompts:
        print(prompt)


if __name__ == "__main__":
    main()
