from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import NewType

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Opaque ID type for problems - str at runtime, distinct in type-checkers.
ProblemId = NewType("ProblemId", str)


class Actor(StrEnum):
    candidate = "candidate"
    challenger = "challenger"
    examiner = "examiner"
    system = "system"


class TurnKind(StrEnum):
    question = "question"
    answer = "answer"
    probe = "probe"
    defense = "defense"
    submit = "submit"
    spoken_question = "spoken_question"
    spoken_answer = "spoken_answer"
    spoken_probe = "spoken_probe"


class Dimension(StrEnum):
    problem_framing = "problem_framing"
    model_rationale = "model_rationale"
    experiment_design = "experiment_design"
    insight_interp = "insight_interp"
    communication = "communication"
    response_authenticity = "response_authenticity"


class ArtifactKind(StrEnum):
    prompt = "prompt"
    markdown = "markdown"
    code_cell = "code_cell"
    cell_output = "cell_output"
    chart_png = "chart_png"
    chat = "chat"
    audio_ref = "audio_ref"
    transcript = "transcript"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Turn(_Frozen):
    id: str
    actor: Actor
    kind: TurnKind
    prompt_ref: str | None
    produced_artifact_refs: tuple[str, ...]
    at: datetime


class Artifact(_Frozen):
    id: str
    kind: ArtifactKind
    version: int = Field(ge=1)
    body: str
    produced_by_turn_id: str
    at: datetime


class Signal(_Frozen):
    id: str = ""
    dimension: Dimension
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: tuple[str, ...]
    emitted_by: str
    justification: str = "evidence recorded"
    at: datetime

    @model_validator(mode="after")
    def _scorer_justification_non_empty(self) -> Signal:
        if "scorer" in self.emitted_by and not self.justification.strip():
            raise ValueError("scorer signals require non-empty justification")
        return self


class Rubric(_Frozen):
    version: str
    weights: Mapping[Dimension, float]

    @field_validator("weights")
    @classmethod
    def _sum_to_one(cls, v: Mapping[Dimension, float]) -> Mapping[Dimension, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"rubric weights must sum to 1.0, got {total}")
        return v


class Score(_Frozen):
    session_id: str
    rubric_version: str
    per_dimension: Mapping[Dimension, float]
    composite: float = Field(ge=0.0, le=1.0)
    at: datetime


class Problem(_Frozen):
    """A single problem statement within a multi-problem session.

    ``id``                   - opaque identifier, unique within a session.
    ``opener_text``          - the narrow first question shown to the candidate.
    ``context``              - full background revealed only to the examiner.
    ``target_dimensions``    - rubric dims this problem is designed to probe.
    ``dim_thresholds``       - per-dim signal threshold at which coverage is
                               considered saturated (0..1 float per Dimension).
    ``expected_duration_s``  - soft time budget in seconds; used for pacing
                               guidance, NOT a hard cut-off.
    """

    id: ProblemId
    opener_text: str
    context: str = ""
    target_dimensions: tuple[Dimension, ...] = ()
    dim_thresholds: Mapping[Dimension, float] = {}
    expected_duration_s: int = Field(default=300, ge=0)


class Session(_Frozen):
    id: str
    rubric_version: str
    started_at: datetime
    ended_at: datetime | None = None
    turns: tuple[Turn, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    # Phase 2.1 - multi-problem fields (empty for legacy sessions)
    problems: tuple[Problem, ...] = ()
    current_problem_id: ProblemId | None = None
