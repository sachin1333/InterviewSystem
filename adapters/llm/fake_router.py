from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from typing import TypedDict


class FakeRouterCall(TypedDict):
    tier: str
    prompt_hash: str
    stream: bool
    timeout: float | None


class FakeRouter:
    """Deterministic fake LLM provider used for chaos testing."""

    def __init__(
        self,
        *,
        fail_mode: str | None = None,
        latency: float = 0.0,
        scripted_responses: Mapping[str, Sequence[str]] | None = None,
        failure_modes: Mapping[str, str] | None = None,
    ) -> None:
        self.fail_mode = fail_mode
        self.latency = latency
        self.calls: list[FakeRouterCall] = []
        self._scripted_responses = {
            prompt_hash: list(responses)
            for prompt_hash, responses in (scripted_responses or {}).items()
        }
        self._failure_modes = dict(failure_modes or {})

    @staticmethod
    def prompt_hash(prompt: str) -> str:
        return hashlib.sha1(prompt.encode("utf8")).hexdigest()[:8]

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        prompt_hash = self.prompt_hash(prompt)
        self.calls.append(
            {
                "tier": tier,
                "prompt_hash": prompt_hash,
                "stream": stream,
                "timeout": timeout,
            }
        )
        if self.latency:
            time.sleep(self.latency)

        fail_mode = self._failure_modes.get(prompt_hash, self.fail_mode)
        if fail_mode == "timeout":
            raise TimeoutError("simulated timeout")
        if fail_mode == "5xx":
            raise RuntimeError("5xx")
        if fail_mode == "rate_limit":
            raise RuntimeError("rate_limit")
        if fail_mode == "malformed":
            return "}{ not a valid JSON or expected shape"
        if fail_mode == "empty":
            return ""

        scripted = self._scripted_responses.get(prompt_hash)
        if scripted:
            response = scripted.pop(0)
            if not scripted:
                self._scripted_responses.pop(prompt_hash, None)
            return response

        return (
            '{"turn_kind":"question",'
            f'"prompt_markdown":"Generated prompt {prompt_hash}",'
            '"artifact_refs":[],'
            '"soft_deadline_minutes":45}'
        )
