from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from adapters.llm.structured_schema import scoring_result_schema
from adapters.scorer.problem_scorer import ProblemLlmScorer
from core.domain import Artifact, ArtifactKind, Dimension, ProblemId


class _RecordingRouter:
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

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
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "tier": tier,
                "prompt": prompt,
                "schema": schema,
                "deadline_ms": deadline_ms,
                "json_schema": json_schema,
                "schema_name": schema_name,
                "max_completion_tokens": max_completion_tokens,
            }
        )
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _artifact(artifact_id: str, body: str) -> Artifact:
    return Artifact(
        id=artifact_id,
        kind=ArtifactKind.markdown,
        version=1,
        body=body,
        produced_by_turn_id=f"turn-{artifact_id}",
        at=datetime(2026, 5, 4, tzinfo=UTC),
    )


def test_score_problem_makes_one_structured_call_for_multiple_dimensions() -> None:
    router = _RecordingRouter(
        {
            "signals": [
                {
                    "dimension": "problem_framing",
                    "value": 0.8,
                    "confidence": 0.7,
                    "source_refs": ["artifact://artifact-a", "not-a-candidate-artifact"],
                    "justification": "Frames the target user and objective.",
                },
                {
                    "dimension": "communication",
                    "value": 0.6,
                    "confidence": 0.9,
                    "source_refs": ["artifact-b"],
                    "justification": "Answer is concise and structured.",
                },
            ]
        }
    )
    scorer = ProblemLlmScorer(router)
    artifacts = (
        _artifact("artifact-a", "We need to predict churn for enterprise users."),
        _artifact("artifact-b", "Recommendation: launch a measured experiment."),
    )
    dimensions = (Dimension.problem_framing, Dimension.communication)

    result = scorer.score_problem(
        "sess-1",
        ProblemId("problem-1"),
        "Problem context: evaluate product churn strategy.",
        artifacts,
        dimensions,
    )

    assert len(router.calls) == 1
    assert router.calls[0]["schema"] == {"signals"}
    assert router.calls[0]["json_schema"] == scoring_result_schema(
        ("problem_framing", "communication")
    )
    assert router.calls[0]["schema_name"] == "problem_scoring_result"
    assert router.calls[0]["max_completion_tokens"] == 700
    assert "Problem ID: problem-1" in router.calls[0]["prompt"]
    assert "artifact-a" in router.calls[0]["prompt"]

    assert result.failures == ()
    assert [(sig.dimension, sig.value, sig.confidence) for sig in result.signals] == [
        (Dimension.problem_framing, 0.8, 0.7),
        (Dimension.communication, 0.6, 0.9),
    ]
    assert result.signals[0].source_refs == ("artifact-a",)
    assert result.signals[1].source_refs == ("artifact-b",)
    assert all(sig.emitted_by == "problem_scorer" for sig in result.signals)


def test_score_problem_falls_back_for_missing_dimension_with_failure() -> None:
    router = _RecordingRouter(
        {
            "signals": [
                {
                    "dimension": "communication",
                    "value": 0.75,
                    "confidence": 0.8,
                    "source_refs": ["artifact-a"],
                    "justification": "Clear answer.",
                }
            ]
        }
    )
    scorer = ProblemLlmScorer(router)

    result = scorer.score_problem(
        "sess-1",
        ProblemId("problem-1"),
        "Context",
        (_artifact("artifact-a", "Hypothesis, metric, experiment, and recommendation."),),
        (Dimension.problem_framing, Dimension.communication),
    )

    assert [sig.dimension for sig in result.signals] == [
        Dimension.problem_framing,
        Dimension.communication,
    ]
    fallback = result.signals[0]
    assert fallback.emitted_by == "problem_scorer:heuristic"
    assert fallback.confidence == 0.2
    assert 0.0 <= fallback.value <= 1.0
    assert fallback.source_refs == ("artifact-a",)
    assert result.failures == (
        pytest_failed(Dimension.problem_framing, "missing scorer signal"),
    )


def test_score_problem_rejects_missing_signals_list_and_falls_back_all_dimensions() -> None:
    router = _RecordingRouter({"not_signals": []})
    scorer = ProblemLlmScorer(router)

    result = scorer.score_problem(
        "sess-1",
        ProblemId("problem-1"),
        "Context",
        (_artifact("artifact-a", "No structured scoring response."),),
        (Dimension.problem_framing, Dimension.communication),
    )

    assert [sig.dimension for sig in result.signals] == [
        Dimension.problem_framing,
        Dimension.communication,
    ]
    assert all(sig.emitted_by == "problem_scorer:heuristic" for sig in result.signals)
    assert all(sig.confidence == 0.2 for sig in result.signals)
    assert [failure.dimension for failure in result.failures] == [
        Dimension.problem_framing,
        Dimension.communication,
    ]
    assert all("signals must be a list" in failure.reason for failure in result.failures)


def test_score_problem_rejects_string_and_out_of_range_scores_without_clamping() -> None:
    router = _RecordingRouter(
        {
            "signals": [
                {
                    "dimension": "problem_framing",
                    "value": "medium",
                    "confidence": 0.8,
                    "source_refs": ["artifact-a"],
                },
                {
                    "dimension": "communication",
                    "value": 1.2,
                    "confidence": 0.8,
                    "source_refs": ["artifact-a"],
                },
            ]
        }
    )
    scorer = ProblemLlmScorer(router)

    result = scorer.score_problem(
        "sess-1",
        ProblemId("problem-1"),
        "Context",
        (_artifact("artifact-a", "This answer has a recommendation and success metric."),),
        (Dimension.problem_framing, Dimension.communication),
    )

    assert all(sig.emitted_by == "problem_scorer:heuristic" for sig in result.signals)
    assert all(0.0 <= sig.value <= 1.0 for sig in result.signals)
    assert [failure.dimension for failure in result.failures] == [
        Dimension.problem_framing,
        Dimension.communication,
    ]
    assert "value must be a number" in result.failures[0].reason
    assert "value must be within [0, 1]" in result.failures[1].reason


def test_score_problem_falls_back_all_dimensions_when_router_raises_malformed_json() -> None:
    router = _RecordingRouter(ValueError("malformed JSON"))
    scorer = ProblemLlmScorer(router)

    result = scorer.score_problem(
        "sess-1",
        ProblemId("problem-1"),
        "Context",
        (_artifact("artifact-a", "Recommendation with rationale."),),
        (Dimension.communication,),
    )

    assert len(router.calls) == 1
    assert len(result.signals) == 1
    assert result.signals[0].dimension is Dimension.communication
    assert result.signals[0].emitted_by == "problem_scorer:heuristic"
    assert result.failures[0].dimension is Dimension.communication
    assert "malformed JSON" in result.failures[0].reason


def pytest_failed(dimension: Dimension, reason: str) -> object:
    from core.events import ScorerFailed

    return ScorerFailed(dimension=dimension, reason=reason)
