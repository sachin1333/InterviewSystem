from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import TypedDict


class FakeRouterCall(TypedDict):
    tier: str
    prompt_hash: str
    stream: bool
    timeout: float | None
    structured: bool


class FakeRouter:
    """Deterministic fake LLM provider used for chaos testing."""

    def __init__(
        self,
        *,
        fail_mode: str | None = None,
        latency: float = 0.0,
        scripted_responses: Mapping[str, Sequence[str]] | None = None,
        failure_modes: Mapping[str, str] | None = None,
        scripted_streams: list[list[str]] | None = None,
    ) -> None:
        self.fail_mode = fail_mode
        self.latency = latency
        self.calls: list[FakeRouterCall] = []
        self._scripted_responses = {
            prompt_hash: list(responses)
            for prompt_hash, responses in (scripted_responses or {}).items()
        }
        self._failure_modes = dict(failure_modes or {})
        self._scripted_streams: list[list[str]] = list(scripted_streams or [])

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
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> str | Iterable[str]:
        del schema_name, max_completion_tokens
        prompt_hash = self.prompt_hash(prompt)
        self.calls.append(
            {
                "tier": tier,
                "prompt_hash": prompt_hash,
                "stream": stream,
                "timeout": timeout,
                "structured": json_schema is not None,
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
            response = "}{ not a valid JSON or expected shape"
        elif fail_mode == "empty":
            response = ""
        else:
            scripted = self._scripted_responses.get(prompt_hash)
            if scripted:
                response = scripted.pop(0)
                if not scripted:
                    self._scripted_responses.pop(prompt_hash, None)
            else:
                response = (
                    '{"turn_kind":"question",'
                    f'"prompt_markdown":"Generated prompt {prompt_hash}",'
                    '"artifact_refs":[],'
                    '"soft_deadline_minutes":45}'
                )

        # scripted_streams take priority for streaming calls
        if stream and self._scripted_streams:
            return iter(self._scripted_streams.pop(0))

        if stream:
            return self._chunk(response)
        return response

    @staticmethod
    def _chunk(text: str) -> Iterable[str]:
        if not text:
            yield ""
            return
        parts = text.split(" ")
        for i, p in enumerate(parts):
            yield p if i == 0 else " " + p
