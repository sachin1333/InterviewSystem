from __future__ import annotations

from core.observability import AuditLogger, MetricSink


def test_metric_sink_exports_prometheus_text() -> None:
    sink = MetricSink()
    sink.increment("chat_requests_total", labels={"route": "/sessions"})
    sink.observe("chat_request_ms", 12.4, labels={"route": "/sessions"})
    text = sink.export_prometheus()
    assert 'chat_requests_total{route="/sessions"} 1' in text
    assert 'chat_request_ms_count{route="/sessions"} 1' in text


def test_audit_logger_hashes_without_raw_text() -> None:
    logger = AuditLogger()
    logger.record_llm_call(agent_name="examiner", model_id="fake", prompt="secret prompt", response="secret response")
    exported = logger.export_jsonl()
    assert "secret prompt" not in exported
    assert "secret response" not in exported
    assert "prompt_hash" in exported
    assert "response_hash" in exported
