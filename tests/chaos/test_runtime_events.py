from adapters.runtime.runtime_adapter import RuntimeAdapter
from core.eventlog import InMemoryEventLog


def test_runtime_adapter_emits_events_on_success() -> None:
    log = InMemoryEventLog()
    ra = RuntimeAdapter(log, memory_limit_bytes=64 * 1024 * 1024, cpu_seconds=2)
    code = "print('hello')\n"
    ra.execute("sess1", "t1", code, timeout_seconds=5)

    events = log.get_session("sess1")
    assert len(events) == 2
    assert events[0].payload.__class__.__name__ == "RuntimeExecuted"
    assert events[1].payload.__class__.__name__ == "ArtifactAttached"


def test_runtime_adapter_emits_failed_event_on_timeout() -> None:
    log = InMemoryEventLog()
    ra = RuntimeAdapter(log, memory_limit_bytes=64 * 1024 * 1024, cpu_seconds=1)
    code = "while True:\n    pass\n"
    ra.execute("sess2", "t2", code, timeout_seconds=1)

    events = log.get_session("sess2")
    assert len(events) == 1
    assert events[0].payload.__class__.__name__ == "RuntimeFailed"
