from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((labels or {}).items()))


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"


@dataclass
class MetricSink:
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(default_factory=lambda: defaultdict(int))
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(default_factory=lambda: defaultdict(list))
    _lock: Lock = field(default_factory=Lock)

    def increment(self, name: str, value: int = 1, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self.counters[(name, _labels_key(labels))] += value

    def observe(self, name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self.histograms[(name, _labels_key(labels))].append(value)

    def export_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lines.append(f"{name}{_format_labels(labels)} {value}")
            for (name, labels), values in sorted(self.histograms.items()):
                label_text = _format_labels(labels)
                lines.append(f"{name}_count{label_text} {len(values)}")
                lines.append(f"{name}_sum{label_text} {sum(values):.6f}")
        return "\n".join(lines) + ("\n" if lines else "")


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
