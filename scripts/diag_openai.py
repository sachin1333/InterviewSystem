"""One-shot diagnostic: load .env, instantiate OpenAIRouter, fire one call.

Run from repo root:
    uv run python scripts/diag_openai.py
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)

from adapters.llm.openai_router import OpenAIRouter  # noqa: E402

router = OpenAIRouter()
print(f"model            = {router.model!r}")
print(f"reasoning_effort = {router.reasoning_effort!r}")
print(f"supports_reasoning_effort = {router._supports_reasoning_effort()}")
print(f"api_key_set      = {bool(router.api_key)}")
print()
print("calling OpenAI (timeout=15s) ...")
try:
    out = router.call(tier="top", prompt="Reply with exactly: 'pong'", timeout=15.0)
    print("OK ->", repr(out))
except Exception as exc:  # noqa: BLE001
    print("FAILED:")
    traceback.print_exc()
    sys.exit(1)
