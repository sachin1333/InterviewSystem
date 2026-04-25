from __future__ import annotations

from datetime import UTC, datetime

from adapters.examiner.llm_examiner import LlmExaminer
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


def test_examiner_returns_probe_outcome_from_llm_json() -> None:
    router = ModelRouter(
        _StaticProvider(
            '{"turn_kind":"probe","text":"Why is that baseline enough?","source_ref":"turn://turn-1","signals":[]}'
        ),
        sleep=lambda _: None,
    )
    examiner = LlmExaminer(router)

    outcome, failure = examiner.review("sess-1", [_turn()], memory_text="Candidate prefers simple models.")

    assert failure is None
    assert outcome.ok_to_advance is False
    assert outcome.probe_text == "Why is that baseline enough?"
    assert outcome.source_ref == "turn://turn-1"


def test_examiner_advances_on_malformed_output() -> None:
    router = ModelRouter(_MalformedProvider(), sleep=lambda _: None)
    examiner = LlmExaminer(router)

    outcome, failure = examiner.review("sess-2", [_turn()])

    assert outcome.ok_to_advance is True
    assert outcome.probe_text is None
    assert failure is not None
