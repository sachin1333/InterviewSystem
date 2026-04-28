from __future__ import annotations

import tomllib
from pathlib import Path


def test_dev_extra_installs_ci_tools() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    dev_extra = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(req.startswith("ruff") for req in dev_extra)
    assert any(req.startswith("mypy") for req in dev_extra)
    assert any(req.startswith("pytest") for req in dev_extra)
