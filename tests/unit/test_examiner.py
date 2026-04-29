"""Unit tests for LlmExaminer — Phase 2.2 schema.

Examiner now returns probe-or-close JSON:
  {"action": "probe"|"close", "text"?: str, "reason"?: str,
   "rationale": str, "primitive_hint"?: str}
"""
from __future__ import annotations

from datetime import UTC, datetime

from adapters.examiner.llm_examiner import CoverageContext, LlmExaminer
from adapters.llm.router import ModelRouter
from core.domain import Actor, Turn, TurnKind


class _StaticProvider:
    def __init__(self, response: str) -> None:
        self.response = response

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        return self.response


class _MalformedProvider:
    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        return "not-json"


def _turn() -> Turn:
    return Turn(
        id="turn-1",
        actor=Actor.candidate,
        kind=TurnKind.answer,
        prompt_ref="turn://question-1",
        produced_artifact_refs=("artifact://artifact-1",),
        at=datetime(2026, 4, 22, tzinfo=UTC),
    )


# ------------------------------------------------------------------ #
#  Phase 2.2 schema — probe action                                   #
# ------------------------------------------------------------------ #

def test_examiner_returns_probe_outcome_from_llm_json() -> None:
    router = ModelRouter(
        _StaticProvider(
            '{"action":"probe","text":"Why is that baseline enough?","rationale":"model_rationale under-served","primitive_hint":"socratic_rebuttal"}'
        ),
        sleep=lambda _: None,
    )
    examiner = LlmExaminer(router)

    outcome, failure = examiner.review("sess-1", [_turn()], memory_text="Candidate prefers simple models.")

    assert failure is None
    assert outcome.action == "probe"
    assert outcome.ok_to_advance is False
    assert outcome.probe_text == "Why is that baseline enough?"
    assert outcome.rationale == "model_rationale under-served"
    assert outcome.primitive_hint == "socratic_rebuttal"


def test_examiner_uses_configured_task_tier_for_probe_decision() -> None:
    tiers: list[str] = []

    class _TierCapturingProvider:
        def call(self, *, tier, prompt, stream=False, timeout=None):
            del prompt, stream, timeout
            tiers.append(tier)
            return '{"action":"probe","text":"What trade-off worries you most?","rationale":"follow-up"}'

    router = ModelRouter(_TierCapturingProvider(), sleep=lambda _: None)
    examiner = LlmExaminer(router)

    outcome, failure = examiner.review("sess-tier", [_turn()])

    assert failure is None
    assert outcome.action == "probe"
    assert tiers == ["mid"]


def test_examiner_returns_close_outcome() -> None:
    router = ModelRouter(
        _StaticProvider(
            '{"action":"close","reason":"coverage_saturated","rationale":"all dims above threshold"}'
        ),
        sleep=lambda _: None,
    )
    examiner = LlmExaminer(router)

    outcome, failure = examiner.review("sess-3", [_turn()])

    assert failure is None
    assert outcome.action == "close"
    assert outcome.ok_to_advance is True
    assert outcome.close_reason == "coverage_saturated"
    assert outcome.probe_text is None


def test_examiner_close_with_unknown_reason_defaults_to_pivot() -> None:
    router = ModelRouter(
        _StaticProvider('{"action":"close","reason":"BOGUS","rationale":"x"}'),
        sleep=lambda _: None,
    )
    examiner = LlmExaminer(router)
    outcome, failure = examiner.review("sess-4", [])
    assert failure is None
    assert outcome.close_reason == "examiner_pivot"


def test_examiner_advances_on_malformed_output() -> None:
    """Both retry attempts fail → returns ok_to_advance + failure."""
    router = ModelRouter(_MalformedProvider(), sleep=lambda _: None)
    examiner = LlmExaminer(router)

    outcome, failure = examiner.review("sess-2", [_turn()])

    assert outcome.ok_to_advance is True
    assert outcome.probe_text is None
    assert failure is not None


def test_examiner_probe_requires_nonempty_text() -> None:
    router = ModelRouter(
        _StaticProvider('{"action":"probe","text":"","rationale":"x"}'),
        sleep=lambda _: None,
    )
    examiner = LlmExaminer(router)
    outcome, failure = examiner.review("sess-5", [])
    # Empty probe text → treated as failure after both retries
    assert outcome.ok_to_advance is True
    assert failure is not None


# ------------------------------------------------------------------ #
#  Coverage context injection                                         #
# ------------------------------------------------------------------ #

def test_examiner_injects_coverage_into_prompt() -> None:
    """When a CoverageContext is provided, the prompt includes coverage state."""
    captured: list[str] = []

    class _CapturingProvider:
        def call(self, *, tier, prompt, stream=False, timeout=None):
            captured.append(prompt)
            return '{"action":"probe","text":"Explain your metric choice.","rationale":"experiment_design under-served"}'

    router = ModelRouter(_CapturingProvider(), sleep=lambda _: None)
    examiner = LlmExaminer(router)
    ctx = CoverageContext(
        problem_id="p1",
        under_served_dims=("experiment_design", "communication"),
        signal_map={"problem_framing": 0.9, "experiment_design": 0.1},
        probe_count=2,
        max_probes=6,
    )
    outcome, failure = examiner.review("sess-cov", [], coverage=ctx)

    assert failure is None
    assert outcome.action == "probe"
    assert captured, "no prompt was captured"
    prompt = captured[0]
    assert "experiment_design" in prompt
    assert "Probes issued: 2 / 6" in prompt


def test_coverage_context_includes_problem_guidance_in_prompt() -> None:
    captured: list[str] = []

    class _CapturingProvider:
        def call(self, *, tier, prompt, stream=False, timeout=None):
            del tier, stream, timeout
            captured.append(prompt)
            return '{"action":"probe","text":"Why?","rationale":"need more"}'

    router = ModelRouter(_CapturingProvider(), sleep=lambda _: None)
    examiner = LlmExaminer(router)
    coverage = CoverageContext(
        problem_id="p1",
        under_served_dims=("problem_framing",),
        signal_map={"problem_framing": 0.4},
        probe_count=1,
        max_probes=6,
        problem_transcript="Candidate: I would define churn.",
        problem_context="Look for metric clarity and cohort definition.",
        target_dimensions=("problem_framing", "communication"),
        expected_duration_s=420,
    )

    examiner.review("s1", [], coverage=coverage)

    assert captured
    prompt = captured[0]
    assert "Look for metric clarity and cohort definition." in prompt
    assert "Target dimensions: problem_framing, communication" in prompt
    assert "Expected duration: 420s" in prompt
