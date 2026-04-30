from __future__ import annotations

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.examiner.llm_examiner import LlmExaminer
from adapters.llm.router import ModelRouter


class _CapturingProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, stream, timeout
        self.prompts.append(prompt)
        return self.response


def test_challenger_includes_user_md_when_composing_prompt() -> None:
    provider = _CapturingProvider('{"prompt_markdown":"Question?","turn_kind":"question"}')
    challenger = LlmChallenger(ModelRouter(provider, sleep=lambda _: None))

    assert list(challenger.propose_prompts("sess-1", user_context="## Declared skills\n- PyTorch")) == ["Question?"]

    assert "## Candidate profile" in provider.prompts[-1]
    assert "PyTorch" in provider.prompts[-1]


def test_examiner_includes_user_md_when_composing_probe_prompt() -> None:
    provider = _CapturingProvider(
        '{"action":"probe","text":"Tell me about your ranking model.","rationale":"resume claim"}'
    )
    examiner = LlmExaminer(ModelRouter(provider, sleep=lambda _: None))

    outcome, failure = examiner.review("sess-1", [], user_context="## Claims\n- Led ranking model")

    assert failure is None
    assert outcome.probe_text == "Tell me about your ranking model."
    assert "## Candidate profile" in provider.prompts[-1]
    assert "Led ranking model" in provider.prompts[-1]
