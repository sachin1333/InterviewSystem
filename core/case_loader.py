from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from core.primitives import Primitive


@dataclass(frozen=True)
class CaseStage:
    id: str
    primitive: Primitive
    duration_s: int
    prompt_seed: str
    on_complete: str  # next stage id, or "end"


@dataclass(frozen=True)
class CaseDefinition:
    version: str
    name: str
    total_duration_min: int
    stages: tuple[CaseStage, ...]

    def stage_sequence(self) -> tuple[CaseStage, ...]:
        """Walk on_complete pointers from the first stage; raise if cycle/dangling."""
        if not self.stages:
            return ()
        by_id = {s.id: s for s in self.stages}
        ordered: list[CaseStage] = []
        seen: set[str] = set()
        cur = self.stages[0]
        while True:
            if cur.id in seen:
                raise ValueError(f"cycle detected at stage {cur.id!r}")
            seen.add(cur.id)
            ordered.append(cur)
            if cur.on_complete == "end":
                break
            nxt = by_id.get(cur.on_complete)
            if nxt is None:
                raise ValueError(f"stage {cur.id!r} points to unknown next {cur.on_complete!r}")
            cur = nxt
        return tuple(ordered)


def load_case(path: str | Path) -> CaseDefinition:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    stages = tuple(
        CaseStage(
            id=s["id"],
            primitive=Primitive(s["primitive"]),
            duration_s=int(s["duration_s"]),
            prompt_seed=str(s["prompt_seed"]),
            on_complete=str(s["on_complete"]),
        )
        for s in raw["stages"]
    )
    return CaseDefinition(
        version=str(raw["version"]),
        name=str(raw["name"]),
        total_duration_min=int(raw["total_duration_min"]),
        stages=stages,
    )
