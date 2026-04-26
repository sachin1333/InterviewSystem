from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adapters.eventlog.sqlite_log import SqliteEventLog
from core.events import LatencyObserved

_COLUMNS = (
    "stt_first_partial_ms",
    "llm_ttft_ms",
    "tts_first_byte_ms",
    "end_to_end_ms",
)


@dataclass(frozen=True)
class LatencySummary:
    p50: int
    p95: int
    max: int


def parse_since(value: str | None, *, now: datetime | None = None) -> datetime | None:
    if not value:
        return None
    now = now or datetime.now(UTC)
    amount = int(value[:-1])
    unit = value[-1]
    delta = {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }.get(unit)
    if delta is None:
        raise ValueError(f"unsupported --since value: {value!r}")
    return now - delta


def summarize_latencies(events: Iterable[LatencyObserved]) -> dict[str, LatencySummary]:
    buckets: dict[str, list[int]] = {column: [] for column in _COLUMNS}
    for event in events:
        for column in _COLUMNS:
            buckets[column].append(int(getattr(event, column)))

    return {
        column: LatencySummary(
            p50=_percentile(values, 0.50),
            p95=_percentile(values, 0.95),
            max=max(values) if values else 0,
        )
        for column, values in buckets.items()
    }


def render_markdown(summary: dict[str, LatencySummary]) -> str:
    rows = [
        "| percentile | stt_first_partial_ms | llm_ttft_ms | tts_first_byte_ms | end_to_end_ms |",
        "|---|---:|---:|---:|---:|",
        "| p50 | "
        f"{summary['stt_first_partial_ms'].p50} | "
        f"{summary['llm_ttft_ms'].p50} | "
        f"{summary['tts_first_byte_ms'].p50} | "
        f"{summary['end_to_end_ms'].p50} |",
        "| p95 | "
        f"{summary['stt_first_partial_ms'].p95} | "
        f"{summary['llm_ttft_ms'].p95} | "
        f"{summary['tts_first_byte_ms'].p95} | "
        f"{summary['end_to_end_ms'].p95} |",
        "| max | "
        f"{summary['stt_first_partial_ms'].max} | "
        f"{summary['llm_ttft_ms'].max} | "
        f"{summary['tts_first_byte_ms'].max} | "
        f"{summary['end_to_end_ms'].max} |",
    ]
    return "\n".join(rows)


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * (len(ordered) - 1)))
    return ordered[index]


def _load_latency_events(db_path: Path, *, since: datetime | None) -> list[LatencyObserved]:
    log = SqliteEventLog(db_path)
    events: list[LatencyObserved] = []
    for envelope in log.all():
        if since is not None and envelope.at < since:
            continue
        if isinstance(envelope.payload, LatencyObserved):
            events.append(envelope.payload)
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a markdown report for voice latency events.")
    parser.add_argument("--db", required=True, help="Path to SqliteEventLog database file.")
    parser.add_argument("--since", default=None, help="Relative window like 15m, 1h, or 1d.")
    args = parser.parse_args()

    since = parse_since(args.since)
    events = _load_latency_events(Path(args.db), since=since)
    print(render_markdown(summarize_latencies(events)))


if __name__ == "__main__":
    main()
