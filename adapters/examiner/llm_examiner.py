"""LlmExaminer - Phase 2.2 rewrite.

The examiner now drives problem boundaries.  On every call it returns
**exactly one** JSON object:

  {"action": "probe"|"close", "text"?: str, "reason"?: str,
   "rationale": str, "primitive_hint"?: str}

``probe``  -> post a follow-up question to the candidate.
``close``  -> declare the problem finished; session_runner emits ProblemClosed.

Coverage state (under-served dims, accumulated signals, probe count) is
injected into the prompt on every call so the model can make an informed
probe-or-close decision.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from adapters.llm.structured_schema import examiner_outcome_schema
from adapters.llm.task_tier_map import tier_for
from core.contracts import Examiner
from core.domain import Dimension, Signal, Turn
from core.events import ExaminerFailed

_CLOSE_REASONS = frozenset(
    {"coverage_saturated", "time_capped", "examiner_pivot", "max_probes"}
)


class JsonModelRouter(Protocol):
    def call_json(
        self,
        *,
        tier: Literal["cheap", "mid", "top"],
        prompt: str,
        schema: set[str] | Mapping[str, object],
        deadline_ms: int | None = None,
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> dict[str, Any]: ...

    def iter_streaming(
        self,
        *,
        tier: Literal["cheap", "mid", "top"],
        prompt: str,
    ) -> Iterator[str]: ...


@dataclass(frozen=True)
class CoverageContext:
    """Coverage state passed into the examiner prompt each call.

    All fields have safe defaults so callers can omit them for legacy paths.
    """

    problem_id: str = ""
    under_served_dims: tuple[str, ...] = ()
    signal_map: dict[str, float] = field(default_factory=dict)
    probe_count: int = 0
    max_probes: int = 6
    problem_transcript: str = ""
    problem_context: str = ""
    target_dimensions: tuple[str, ...] = ()
    expected_duration_s: int = 0


@dataclass(frozen=True)
class ExaminerOutcome:
    """Result of one examiner call.

    ``action``       - ``"probe"`` or ``"close"``.
    ``probe_text``   - non-None when action == "probe".
    ``close_reason`` - non-None when action == "close".
    ``rationale``    - examiner's internal note (not shown to candidate).
    ``primitive_hint`` - optional style hint for the probe renderer.

    Legacy fields kept for backward compatibility:
      ``ok_to_advance`` - True when action == "close" (or on failure).
      ``source_ref``    - always None in Phase 2.2.
      ``signals``       - always empty in Phase 2.2 (coverage tracker owns signals).
    """

    action: Literal["probe", "close"] = "probe"
    probe_text: str | None = None
    close_reason: str | None = None
    rationale: str = ""
    primitive_hint: str | None = None
    # Legacy compat
    ok_to_advance: bool = False
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
        coverage: CoverageContext | None = None,
        user_context: str = "",
    ) -> tuple[ExaminerOutcome, ExaminerFailed | None]:
        """Call the LLM and parse a probe-or-close decision.

        Uses the router's bounded JSON retry before returning a failure outcome.
        """
        prompt = self._compose_prompt(
            session_id, recent_turns, memory_text, coverage=coverage, user_context=user_context
        )
        # Only "action" is strictly required; other fields are action-dependent.
        # "rationale" is also expected but tolerated as empty string on miss.
        schema: set[str] = {"action"}

        try:
            payload = self.model_router.call_json(
                tier=tier_for("examiner.probe"),
                prompt=prompt,
                schema=schema,
                deadline_ms=2000,
                json_schema=examiner_outcome_schema(),
                schema_name="examiner_outcome",
                max_completion_tokens=500,
            )
            outcome = self._parse_outcome(payload)
            return outcome, None
        except Exception as exc:
            return (
                ExaminerOutcome(ok_to_advance=True),
                ExaminerFailed(reason=str(exc) or exc.__class__.__name__),
            )

    def iter_review(
        self,
        session_id: str,
        recent_turns: Sequence[Any],
        *,
        coverage: CoverageContext | None = None,
        user_context: str = "",
    ) -> Iterator[str]:
        """Stream probe text token-by-token (SSE path).

        Yields all raw tokens from the model.  The session_runner is
        responsible for calling ``review()`` separately to obtain the parsed
        ``ExaminerOutcome`` and decide whether to emit ``ProblemClosed``.
        """
        prompt = self._compose_prompt(
            session_id,
            [],           # TurnInfo dicts from projections != Turn domain objects
            memory_text="",
            coverage=coverage,
            user_context=user_context,
        )
        yield from self.model_router.iter_streaming(tier="mid", prompt=prompt)

    # ------------------------------------------------------------------ #
    #  Prompt composition                                                  #
    # ------------------------------------------------------------------ #

    def _compose_prompt(
        self,
        session_id: str,
        recent_turns: Sequence[Turn],
        memory_text: str,
        *,
        coverage: CoverageContext | None = None,
        user_context: str = "",
    ) -> str:
        parts = [
            self._load_template("IDENTITY.md"),
            self._load_template("SOUL.md"),
            self._load_template("TOOLS.md"),
            self._load_template("PACING.md"),
            f"Session: {session_id}",
            f"Memory: {memory_text or '(empty)'}",
        ]
        if user_context.strip():
            parts.append("## Candidate profile\n\n" + user_context.strip())

        # ── Coverage state injection (Phase 2.2) ──
        if coverage is not None:
            parts.append(_format_coverage_context(coverage))

        # ── Problem-scoped transcript ──
        if coverage is not None and coverage.problem_transcript:
            parts.append(
                "## Problem transcript (this problem only)\n\n"
                + coverage.problem_transcript
            )

        # ── Recent domain Turn objects (non-SSE path only) ──
        if recent_turns:
            turn_lines = [
                f"- {t.actor.value}:{t.kind.value}:{t.id} refs={list(t.produced_artifact_refs)}"
                for t in recent_turns
            ]
            parts.append("## Recent turns\n\n" + "\n".join(turn_lines))

        return "\n\n".join(parts)

    def _load_template(self, name: str) -> str:
        path = self.templates_root / name
        try:
            return path.read_text(encoding="utf8")
        except OSError:
            return ""

    # ------------------------------------------------------------------ #
    #  Output parsing                                                      #
    # ------------------------------------------------------------------ #

    def _parse_outcome(self, payload: dict[str, Any]) -> ExaminerOutcome:
        action = payload.get("action")
        text = payload.get("text")
        reason = payload.get("reason")
        rationale = str(payload.get("rationale") or "")
        primitive_hint = payload.get("primitive_hint") or None

        if action == "probe":
            if not isinstance(text, str) or not text.strip():
                raise ValueError("probe action requires non-empty 'text'")
            return ExaminerOutcome(
                action="probe",
                probe_text=text.strip(),
                rationale=rationale,
                primitive_hint=primitive_hint,
                ok_to_advance=False,
            )

        if action == "close":
            close_reason = reason if reason in _CLOSE_REASONS else "examiner_pivot"
            return ExaminerOutcome(
                action="close",
                close_reason=close_reason,
                rationale=rationale,
                primitive_hint=primitive_hint,
                ok_to_advance=True,
            )

        raise ValueError(f"unknown examiner action: {action!r}")

    @staticmethod
    def _parse_signal(raw_signal: object) -> Signal | None:
        """Legacy helper - kept for backward compatibility; not called in 2.2."""
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


# ------------------------------------------------------------------ #
#  Coverage context formatter                                         #
# ------------------------------------------------------------------ #

def _format_coverage_context(ctx: CoverageContext) -> str:
    lines = [
        "## Coverage state",
        f"Problem: {ctx.problem_id or '(unknown)'}",
        f"Probes issued: {ctx.probe_count} / {ctx.max_probes}",
    ]
    if ctx.problem_context:
        lines.append("Problem guidance: " + ctx.problem_context)
    if ctx.target_dimensions:
        lines.append("Target dimensions: " + ", ".join(ctx.target_dimensions))
    if ctx.expected_duration_s:
        lines.append(f"Expected duration: {ctx.expected_duration_s}s")
    if ctx.under_served_dims:
        lines.append(
            "Under-served dimensions (prioritise these): "
            + ", ".join(ctx.under_served_dims)
        )
    else:
        lines.append("Coverage: SATURATED - all dimensions above threshold.")

    if ctx.signal_map:
        lines.append("Accumulated signal per dimension:")
        for dim, sig in sorted(ctx.signal_map.items()):
            lines.append(f"  {dim}: {sig:.2f}")

    return "\n".join(lines)
