from __future__ import annotations

from pathlib import Path

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.llm.router import ModelRouter


class _AlwaysFailsProvider:
    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
    ) -> str:
        del tier, prompt, stream, timeout
        raise RuntimeError("boom")


def test_challenger_uses_canned_prompt_fixture_on_failure(tmp_path: Path) -> None:
    templates_root = tmp_path / "templates" / "agents" / "challenger"
    templates_root.mkdir(parents=True)
    for name in ("IDENTITY.md", "SOUL.md", "TOOLS.md"):
        (templates_root / name).write_text(f"{name} contents", encoding="utf8")

    canned_prompts_path = tmp_path / "templates" / "rubrics" / "fixtures" / "canned_prompts.md"
    canned_prompts_path.parent.mkdir(parents=True)
    canned_prompts_path.write_text(
        "Please analyze the attached retention dataset and explain your trade-offs.",
        encoding="utf8",
    )

    router = ModelRouter(_AlwaysFailsProvider(), max_retries=1, sleep=lambda _: None)
    challenger = LlmChallenger(
        router,
        templates_root=templates_root,
        canned_prompts_path=canned_prompts_path,
    )

    assert list(challenger.propose_prompts("session-123")) == [
        "Please analyze the attached retention dataset and explain your trade-offs."
    ]
