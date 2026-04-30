from __future__ import annotations

from adapters.llm.router import ModelRouter
from core.observability import AuditLogger


class _Provider:
    def call(self, *, tier: str, prompt: str, stream: bool = False, timeout: float | None = None) -> str:
        del tier, stream, timeout
        return '{"ok": true}'


def test_model_router_audit_logger_records_hashes_without_raw_text() -> None:
    audit = AuditLogger()
    router = ModelRouter(_Provider(), audit_logger=audit)
    router.call("cheap", "raw candidate secret")
    exported = audit.export_jsonl()
    assert "raw candidate secret" not in exported
    assert "prompt_hash" in exported
