"""Task → default tier mapping.

Overridable via config/tier_overrides.yaml.
"""
from __future__ import annotations

from typing import Literal

type Tier = Literal["cheap", "mid", "top"]

TASK_TIER_MAP: dict[str, Tier] = {
    "challenger.draft": "top",
    "examiner.probe": "top",
    "scorer.rationale": "top",
    "scorer.communication": "mid",
    "scorer.insight_interp": "top",
    "scorer.problem_framing": "mid",
    "intake.resume_parse": "cheap",
    "guardrail.json_repair": "cheap",
}


def tier_for(task: str) -> Tier:
    """Return the tier for a task key, defaulting to 'mid'."""
    return TASK_TIER_MAP.get(task, "mid")
