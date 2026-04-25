"""Tests for core/pack_loader.py."""

import pytest

from core.pack_loader import PackRegistry, load_pack


def test_load_ds_ml_v1_pack() -> None:
    pack = load_pack("ds-ml-v1")
    assert pack.id == "ds-ml-v1"
    assert pack.version
    assert pack.display_name
    assert pack.rubric is not None
    assert pack.templates_root.exists()


def test_pack_registry_caches() -> None:
    reg = PackRegistry()
    p1 = reg.get("ds-ml-v1")
    p2 = reg.get("ds-ml-v1")
    assert p1 is p2  # same object from cache


def test_pack_registry_available() -> None:
    reg = PackRegistry()
    ids = reg.available()
    assert "ds-ml-v1" in ids


def test_load_unknown_pack_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_pack("nonexistent-pack-xyz")
