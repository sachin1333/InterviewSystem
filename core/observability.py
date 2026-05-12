from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock

_UNSAFE_LABEL_TERMS = ("prompt", "resume", "candidate", "answer")


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    safe_labels = {
        key: value
        for key, value in (labels or {}).items()
        if not any(term in key.lower() for term in _UNSAFE_LABEL_TERMS)
    }
    return tuple(sorted(safe_labels.items()))


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in labels) + "}"


@dataclass
class MetricSink:
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(default_factory=lambda: defaultdict(int))
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(default_factory=lambda: defaultdict(list))
    gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def increment(self, name: str, value: int = 1, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self.counters[(name, _labels_key(labels))] += value

    def observe(self, name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self.histograms[(name, _labels_key(labels))].append(value)

    def set_gauge(self, name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self.gauges[(name, _labels_key(labels))] = value

    def observe_chat_request(self, *, route: str, elapsed_ms: float) -> None:
        self.increment("chat_requests_total", labels={"route": route})
        self.observe("chat_request_ms", elapsed_ms, labels={"route": route})

    def observe_llm_call(self, *, component: str, elapsed_ms: float, outcome: str) -> None:
        self.observe("llm_call_ms", elapsed_ms, labels={"component": component, "outcome": outcome})

    def set_scoring_queue_depth(self, depth: int) -> None:
        self.set_gauge("scoring_queue_depth", depth)

    def observe_scoring_job(self, *, elapsed_ms: float, outcome: str) -> None:
        self.observe("scoring_job_ms", elapsed_ms, labels={"outcome": outcome})

    def increment_structured_output_failure(self, *, component: str, reason: str) -> None:
        self.increment(
            "structured_output_failures_total",
            labels={"component": component, "reason": reason},
        )

    def export_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), counter_value in sorted(self.counters.items()):
                lines.append(f"{name}{_format_labels(labels)} {counter_value}")
            for (name, labels), gauge_value in sorted(self.gauges.items()):
                lines.append(f"{name}{_format_labels(labels)} {_format_number(gauge_value)}")
            for (name, labels), values in sorted(self.histograms.items()):
                label_text = _format_labels(labels)
                lines.append(f"{name}_count{label_text} {len(values)}")
                lines.append(f"{name}_sum{label_text} {sum(values):.6f}")
        return "\n".join(lines) + ("\n" if lines else "")


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.6f}"


@dataclass(frozen=True)
class AuditRecord:
    at: str
    agent_name: str
    model_id: str
    prompt_hash: str
    response_hash: str
    tokens_in: int
    tokens_out: int


@dataclass
class AuditLogger:
    records: list[AuditRecord] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def record_llm_call(self, *, agent_name: str, model_id: str, prompt: str, response: str) -> None:
        rec = AuditRecord(
            at=datetime.now(UTC).isoformat(),
            agent_name=agent_name,
            model_id=model_id,
            prompt_hash=_sha(prompt),
            response_hash=_sha(response),
            tokens_in=len(prompt.split()),
            tokens_out=len(response.split()),
        )
        with self._lock:
            self.records.append(rec)

    def export_jsonl(self) -> str:
        with self._lock:
            return "".join(json.dumps(rec.__dict__, sort_keys=True) + "\n" for rec in self.records)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()


GLOBAL_METRICS = MetricSink()
GLOBAL_AUDIT_LOGGER = AuditLogger()
