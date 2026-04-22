import pytest
from pydantic import ValidationError

from core import domain, rubric_loader


def test_load_valid_rubric_from_file():
    r = rubric_loader.load_rubric(yaml_path="templates/rubrics/ds-ml-engineer-v1.yaml")
    assert isinstance(r, domain.Rubric)
    assert r.version == "ds-ml-engineer@1"
    assert abs(sum(r.weights.values()) - 1.0) < 1e-6
    assert domain.Dimension.communication in r.weights


def test_invalid_weights_raise_validation_error():
    bad_yaml = """
name: bad-rubric
version: 1
dimensions:
  - name: problem_framing
    weight: 0.6
  - name: model_rationale
    weight: 0.6
"""
    with pytest.raises(ValidationError):
        rubric_loader.load_rubric(yaml_str=bad_yaml)
