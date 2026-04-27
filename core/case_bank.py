from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CaseEntry:
    body: str


@dataclass
class CaseBank:
    _entries: list[CaseEntry]

    @classmethod
    def from_yaml(cls, path: str | Path) -> CaseBank:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        entries = [CaseEntry(body=str(e["body"])) for e in raw.get("cases", [])]
        return cls(_entries=entries)

    def __len__(self) -> int:
        return len(self._entries)

    def pick_for_session(self, session_id: str) -> CaseEntry:
        if not self._entries:
            raise ValueError("empty case bank")
        return self._entries[hash(session_id) % len(self._entries)]
