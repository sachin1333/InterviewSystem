from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from core.case_bank import CaseBank

from core.contracts import Challenger


class JsonModelRouter(Protocol):
    def call_json(
        self,
        *,
        tier: Literal["cheap", "mid", "top"],
        prompt: str,
        schema: set[str] | Mapping[str, object],
        deadline_ms: int | None = None,
    ) -> dict[str, Any]: ...


class LlmChallenger(Challenger):
    def __init__(
        self,
        model_router: JsonModelRouter,
        *,
        templates_root: str | Path | None = None,
        canned_prompts_path: str | Path | None = None,
        case_bank: CaseBank | None = None,
    ) -> None:
        self.model_router = model_router
        self.templates_root = (
            Path(templates_root)
            if templates_root is not None
            else Path("templates") / "agents" / "challenger"
        )
        self.canned_prompts_path = (
            Path(canned_prompts_path)
            if canned_prompts_path is not None
            else Path("templates") / "rubrics" / "fixtures" / "canned_prompts.md"
        )
        self.case_bank = case_bank
        self.inline_fallback_prompt = (
            "Please analyze the business problem, state your assumptions, and explain "
            "the trade-offs in your approach."
        )

    def _load_template(self, name: str) -> str:
        path = self.templates_root / name
        try:
            return path.read_text(encoding="utf8")
        except OSError:
            return ""

    def _compose_prompt(self, session_id: str) -> str:
        identity = self._load_template("IDENTITY.md")
        soul = self._load_template("SOUL.md")
        tools = self._load_template("TOOLS.md")
        return "\n\n".join([identity, soul, tools, f"Session: {session_id}"])

    def _fallback_prompt(self) -> str:
        try:
            prompt = self.canned_prompts_path.read_text(encoding="utf8").strip()
        except OSError:
            return self.inline_fallback_prompt
        return prompt or self.inline_fallback_prompt

    def propose_prompts(self, session_id: str) -> Iterable[str]:
        if self.case_bank is not None and len(self.case_bank) > 0:
            try:
                picked = self.case_bank.pick_for_session(session_id)
                return [picked.body]
            except Exception:
                pass  # fall through to live LLM

        try:
            payload = self.model_router.call_json(
                tier="top",
                prompt=self._compose_prompt(session_id),
                schema={"prompt_markdown", "turn_kind"},
                deadline_ms=8000,
            )
        except Exception:
            return [self._fallback_prompt()]

        prompt_markdown = payload.get("prompt_markdown")
        if (
            isinstance(prompt_markdown, str)
            and prompt_markdown.strip()
            and not prompt_markdown.startswith("Fallback prompt:")
        ):
            return [prompt_markdown]
        return [self._fallback_prompt()]
