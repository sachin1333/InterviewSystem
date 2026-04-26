from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    dimension: Dimension
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: tuple[str, ...]
    emitted_by: str
    at: datetime


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


class Session(_Frozen):
    id: str
    rubric_version: str
    started_at: datetime
    ended_at: datetime | None = None
    turns: tuple[Turn, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
