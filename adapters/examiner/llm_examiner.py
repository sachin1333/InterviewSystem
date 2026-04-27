from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from core.contracts import Examiner
from core.domain import Dimension, Signal, Turn
from core.events import ExaminerFailed


class JsonModelRouter(Protocol):
    def call_json(
        self,
        *,
        tier: Literal["cheap", "mid", "top"],
        prompt: str,
        schema: set[str] | Mapping[str, object],
        deadline_ms: int | None = None,
    ) -> dict[str, Any]: ...

    def iter_streaming(
        self,
        *,
        tier: Literal["cheap", "mid", "top"],
        prompt: str,
    ) -> Iterator[str]: ...


@dataclass(frozen=True)
class ExaminerOutcome:
    ok_to_advance: bool
    probe_text: str | None = None
    source_ref: str | None = None
    signals: tuple[Signal, ...] = ()


class LlmExaminer(Examiner):
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
            else Path("templates") / "agents" / "examiner"
        )
        self.last_outcome: ExaminerOutcome | None = None
        self.last_failure: ExaminerFailed | None = None

    def review_turn(self, session_id: str, turn: Turn) -> None:
        self.last_outcome, self.last_failure = self.review(session_id, [turn])

    def review(
        self,
        session_id: str,
        recent_turns: Sequence[Turn],
        *,
        memory_text: str = "",
    ) -> tuple[ExaminerOutcome, ExaminerFailed | None]:
        try:
            payload = self.model_router.call_json(
                tier="top",
                prompt=self._compose_prompt(session_id, recent_turns, memory_text),
                schema={"turn_kind", "text", "source_ref", "signals"},
                deadline_ms=2000,
            )
            outcome = self._parse_outcome(payload)
        except Exception as exc:
            return (
                ExaminerOutcome(ok_to_advance=True),
                ExaminerFailed(reason=str(exc) or exc.__class__.__name__),
            )
        return outcome, None

    def iter_review(
        self,
        session_id: str,
        recent_turns: Sequence[Any],
    ) -> Iterator[str]:
        """Yield probe text token-by-token from the streaming examiner path.

        Passes an empty turn list to _compose_prompt (TurnInfo dicts from
        projections are not compatible with Turn domain objects).  The prompt
        still carries the session_id for context.
        """
        prompt = self._compose_prompt(session_id, [], memory_text="")
        yield from self.model_router.iter_streaming(tier="mid", prompt=prompt)

    def _compose_prompt(
        self,
        session_id: str,
        recent_turns: Sequence[Turn],
        memory_text: str,
    ) -> str:
        parts = [
            self._load_template("IDENTITY.md"),
            self._load_template("SOUL.md"),
            self._load_template("TOOLS.md"),
            self._load_template("PACING.md"),
            f"Session: {session_id}",
            f"Memory: {memory_text or '(empty)'}",
            "Recent turns:",
        ]
        parts.extend(
            f"- {turn.actor.value}:{turn.kind.value}:{turn.id} refs={list(turn.produced_artifact_refs)}"
            for turn in recent_turns
        )
        return "\n\n".join(parts)

    def _load_template(self, name: str) -> str:
        path = self.templates_root / name
        try:
            return path.read_text(encoding="utf8")
        except OSError:
            return ""

    def _parse_outcome(self, payload: dict[str, Any]) -> ExaminerOutcome:
        turn_kind = payload.get("turn_kind")
        text = payload.get("text")
        source_ref = payload.get("source_ref")
        if turn_kind != "probe" or not isinstance(text, str) or not text.strip():
            raise ValueError("invalid examiner payload: missing probe text")
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise ValueError("invalid examiner payload: missing source_ref")

        raw_signals = payload.get("signals")
        signals: list[Signal] = []
        if isinstance(raw_signals, list):
            for raw_signal in raw_signals:
                parsed = self._parse_signal(raw_signal)
                if parsed is not None:
                    signals.append(parsed)

        return ExaminerOutcome(
            ok_to_advance=False,
            probe_text=text,
            source_ref=source_ref,
            signals=tuple(signals),
        )

    @staticmethod
    def _parse_signal(raw_signal: object) -> Signal | None:
        if not isinstance(raw_signal, dict):
            return None
        raw_dimension = raw_signal.get("dimension")
        raw_value = raw_signal.get("value")
        raw_source_ref = raw_signal.get("source_ref")
        if not isinstance(raw_dimension, str) or not isinstance(raw_source_ref, str):
            return None
        if not isinstance(raw_value, (int, float, str)):
            return None
        try:
            dimension = Dimension(raw_dimension)
            value = float(raw_value)
        except (TypeError, ValueError):
            return None
        return Signal(
            dimension=dimension,
            value=value,
            confidence=0.5,
            source_refs=(raw_source_ref,),
            emitted_by="examiner",
            at=datetime.now(UTC),
        )
