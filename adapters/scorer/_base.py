from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol

from core.contracts import Scorer
from core.domain import Artifact, Dimension, Signal
from core.events import ScorerFailed


class JsonModelRouter(Protocol):
    def call_json(
        self,
        *,
        tier: Literal["cheap", "mid", "top"],
        prompt: str,
        schema: set[str] | Mapping[str, object],
        deadline_ms: int | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ScorerResult:
    signal: Signal | None
    failure: ScorerFailed | None


class BaseLlmScorer(Scorer):
    dimension: ClassVar[Dimension]
    scorer_name: ClassVar[str]
    template_dir_name: ClassVar[str]
    heuristic_keywords: ClassVar[tuple[str, ...]]
    heuristic_hit_value: ClassVar[float]
    heuristic_miss_value: ClassVar[float]

    def __init__(
        self,
        model_router: JsonModelRouter,
        *,
        templates_root: str | Path | None = None,
    ) -> None:
        self.model_router = model_router
        self.templates_root = (
            Path(templates_root)
            if templates_root is not None
            else Path("templates") / "agents" / "scorers" / self.template_dir_name
        )

    def score(self, session_id: str, artifact: Artifact, dimension: Dimension) -> Signal:
        if dimension is not self.dimension:
            raise ValueError(
                f"{self.__class__.__name__} only scores {self.dimension}, got {dimension}"
            )

        result = self.score_optional(session_id, artifact)
        if result.signal is not None:
            return result.signal
        return self._heuristic_signal(artifact)

    def score_optional(self, session_id: str, artifact: Artifact) -> ScorerResult:
        try:
            payload = self.model_router.call_json(
                tier="top",
                prompt=self._compose_prompt(session_id, artifact),
                schema={"signals"},
            )
        except Exception as exc:
            return ScorerResult(
                signal=self._heuristic_signal(artifact),
                failure=ScorerFailed(dimension=self.dimension, reason=self._reason(exc)),
            )

        signals_payload = payload.get("signals")
        if not isinstance(signals_payload, list):
            return ScorerResult(
                signal=self._heuristic_signal(artifact),
                failure=ScorerFailed(
                    dimension=self.dimension,
                    reason="invalid scorer payload: signals must be a list",
                ),
            )
        if not signals_payload:
            return ScorerResult(signal=None, failure=None)

        try:
            signal = self._parse_signal(signals_payload[0], artifact)
        except Exception as exc:
            return ScorerResult(
                signal=self._heuristic_signal(artifact),
                failure=ScorerFailed(dimension=self.dimension, reason=self._reason(exc)),
            )
        return ScorerResult(signal=signal, failure=None)

    def _compose_prompt(self, session_id: str, artifact: Artifact) -> str:
        identity = self._load_template("IDENTITY.md")
        soul = self._load_template("SOUL.md")
        tools = self._load_template("TOOLS.md")
        return "\n\n".join(
            [
                identity,
                soul,
                tools,
                f"Session: {session_id}",
                f"Artifact ID: {artifact.id}",
                f"Artifact kind: {artifact.kind}",
                "Artifact body:",
                artifact.body,
            ]
        )

    def _load_template(self, name: str) -> str:
        path = self.templates_root / name
        try:
            return path.read_text(encoding="utf8")
        except OSError:
            return ""

    def _parse_signal(self, raw_signal: object, artifact: Artifact) -> Signal:
        if not isinstance(raw_signal, dict):
            raise ValueError("scorer signal must be a mapping")

        raw_dimension = raw_signal.get("dimension")
        if not isinstance(raw_dimension, str):
            raise ValueError("scorer signal missing dimension")
        dimension = Dimension(raw_dimension)
        if dimension is not self.dimension:
            raise ValueError(
                f"expected scorer dimension {self.dimension}, got {dimension}"
            )

        raw_value = raw_signal.get("value")
        raw_confidence = raw_signal.get("confidence")
        if not isinstance(raw_value, (int, float, str)):
            raise ValueError("scorer signal missing value")
        if not isinstance(raw_confidence, (int, float, str)):
            raise ValueError("scorer signal missing confidence")
        value = float(raw_value)
        confidence = float(raw_confidence)
        raw_refs = raw_signal.get("source_refs")
        source_refs: tuple[str, ...]
        if raw_refs is None:
            source_refs = (f"artifact://{artifact.id}",)
        elif isinstance(raw_refs, list):
            source_refs = tuple(str(ref) for ref in raw_refs)
        else:
            raise ValueError("source_refs must be a list when provided")

        return Signal(
            dimension=self.dimension,
            value=value,
            confidence=confidence,
            source_refs=source_refs or (f"artifact://{artifact.id}",),
            emitted_by=self.scorer_name,
            at=datetime.now(UTC),
        )

    def _heuristic_signal(self, artifact: Artifact) -> Signal:
        body = artifact.body.lower()
        hit = any(keyword in body for keyword in self.heuristic_keywords)
        value = self.heuristic_hit_value if hit else self.heuristic_miss_value
        return Signal(
            dimension=self.dimension,
            value=value,
            confidence=0.2,
            source_refs=(f"artifact://{artifact.id}",),
            emitted_by=f"{self.scorer_name}:heuristic",
            at=datetime.now(UTC),
        )

    @staticmethod
    def _reason(exc: Exception) -> str:
        return str(exc) or exc.__class__.__name__
