from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from core import domain


class RubricLoaderError(Exception):
    """Raised for rubric loader problems (YAML/format/unknown fields)."""


def load_rubric(yaml_path: str | Path | None = None, yaml_str: str | None = None) -> domain.Rubric:
    """
    Load a rubric from a YAML file or YAML string and return a validated `domain.Rubric`.

    Provide exactly one of `yaml_path` or `yaml_str`.
    The returned `domain.Rubric` enforces that weights sum to 1.0 via pydantic validation.
    """
    if bool(yaml_path) == bool(yaml_str):
        raise ValueError("provide exactly one of yaml_path or yaml_str")

    if yaml_path:
        path = Path(yaml_path)
        try:
            text = path.read_text(encoding="utf8")
        except Exception as e:
            raise FileNotFoundError(f"could not read rubric file {yaml_path}") from e
    else:
        text = yaml_str  # type: ignore[assignment]

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RubricLoaderError("failed to parse YAML") from e

    if not isinstance(data, dict):
        raise RubricLoaderError("rubric YAML must be a mapping")

    name = data.get("name")
    version = data.get("version")
    dims = data.get("dimensions")

    if name is None or version is None or dims is None:
        raise RubricLoaderError("rubric YAML missing required fields: name/version/dimensions")

    # allow small set of synonyms between template keys and domain.Dimension names
    synonym_map = {
        "insight_interpretation": "insight_interp",
    }

    weights: dict[domain.Dimension, float] = {}
    if not isinstance(dims, list):
        raise RubricLoaderError("dimensions must be a list")

    for item in dims:
        if not isinstance(item, dict):
            raise RubricLoaderError("each dimension must be a mapping with 'name' and 'weight'")
        raw_name = item.get("name")
        if raw_name is None:
            raise RubricLoaderError("dimension entry missing 'name'")
        assert isinstance(raw_name, str)
        mapped_name = synonym_map.get(raw_name, raw_name)
        try:
            dim = domain.Dimension(mapped_name)
        except ValueError as err:
            raise RubricLoaderError(f"unknown dimension name: {raw_name}") from err
        try:
            weight = float(item.get("weight", 0))
        except Exception as err:
            raise RubricLoaderError(f"invalid weight for dimension {raw_name}") from err
        weights[dim] = weight

    version_id = f"{name}@{version}"
    # Construct domain.Rubric and let pydantic raise ValidationError for invalid weights sum/types
    try:
        rubric = domain.Rubric(version=version_id, weights=weights)
    except ValidationError:
        raise
    return rubric
