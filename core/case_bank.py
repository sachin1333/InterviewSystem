"""Cold-start case-prompt bank.

Loads pre-generated DS/ML case prompts from YAML and picks one deterministically
per session_id. Used by LlmChallenger to render the first interviewer turn
without waiting on a live LLM call.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.primitives import Primitive


class CaseBankEmpty(RuntimeError):
    """Raised when the bank has zero prompts available."""


@dataclass(frozen=True)
class BankedPrompt:
    id: str
    primitive: Primitive
    body: str


class CaseBank:
    def __init__(self, prompts: Sequence[BankedPrompt]) -> None:
        self._prompts: tuple[BankedPrompt, ...] = tuple(prompts)

    @classmethod
    def from_yaml(cls, path: str | Path) -> CaseBank:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf8")) or {}
        items = raw.get("prompts") or []
        prompts: list[BankedPrompt] = []
        for item in items:
            prompts.append(BankedPrompt(
                id=str(item["id"]),
                primitive=Primitive(item.get("primitive", "think_aloud")),
                body=str(item["body"]).strip(),
            ))
        return cls(prompts)

    def __len__(self) -> int:
        return len(self._prompts)

    def pick_for_session(self, session_id: str) -> BankedPrompt:
        if not self._prompts:
            raise CaseBankEmpty("case bank has no prompts")
        digest = hashlib.sha256(session_id.encode("utf8")).digest()
        idx = int.from_bytes(digest[:8], "big") % len(self._prompts)
        return self._prompts[idx]
