"""Pack loader — loads and caches interview packs from disk.

A Pack bundles a rubric, agent templates, tier overrides, and dimension
display names. DS/ML ships as ds-ml-v1; other packs are additive.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.domain import Rubric

_PACKS_ROOT = Path(__file__).parent.parent / "templates" / "packs"


@dataclass(frozen=True)
class Pack:
    """Loaded, validated pack.

    Dimension display names are stored in dim_display_names map.
    The Dimension enum itself is not dynamically modified — display names
    are applied at UI rendering time by the client.
    """

    id: str
    version: str
    display_name: str
    rubric: Rubric
    templates_root: Path          # templates/packs/<id>/
    tier_overrides: dict[str, str]
    dim_display_names: dict[str, str]


def _load_rubric(pack_dir: Path) -> Rubric:
    """Load rubric.yaml from pack dir.  Falls back to legacy path."""
    rubric_path = pack_dir / "rubric.yaml"
    if not rubric_path.exists():
        # Legacy fallback
        rubric_path = (
            Path(__file__).parent.parent
            / "templates" / "rubrics" / "ds-ml-engineer-v1.yaml"
        )
    from core.rubric_loader import load_rubric  # avoid circular at module level
    return load_rubric(rubric_path)


def load_pack(pack_id: str, *, packs_root: Path | None = None) -> Pack:
    """Load a pack by id from disk.  Raises FileNotFoundError if not found."""
    root = packs_root or _PACKS_ROOT
    pack_dir = root / pack_id
    pack_yaml = pack_dir / "pack.yaml"
    if not pack_yaml.exists():
        raise FileNotFoundError(f"Pack not found: {pack_id} (looked in {pack_dir})")

    with pack_yaml.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    rubric = _load_rubric(pack_dir)

    return Pack(
        id=data["id"],
        version=str(data["version"]),
        display_name=data["display_name"],
        rubric=rubric,
        templates_root=pack_dir,
        tier_overrides=dict(data.get("default_tier_overrides") or {}),
        dim_display_names=dict(data.get("dim_display_names") or {}),
    )


class PackRegistry:
    """Lazy-loading, thread-safe pack cache."""

    def __init__(self, packs_root: Path | None = None) -> None:
        self._root = packs_root or _PACKS_ROOT
        self._cache: dict[str, Pack] = {}
        self._lock = threading.Lock()

    def get(self, pack_id: str) -> Pack:
        with self._lock:
            if pack_id not in self._cache:
                self._cache[pack_id] = load_pack(pack_id, packs_root=self._root)
            return self._cache[pack_id]

    def available(self) -> list[str]:
        """Return ids of all packs on disk."""
        if not self._root.exists():
            return []
        return [p.name for p in self._root.iterdir() if (p / "pack.yaml").exists()]


# Module-level default registry.
_default_registry = PackRegistry()


def default_registry() -> PackRegistry:
    return _default_registry
