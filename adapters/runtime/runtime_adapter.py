from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

from adapters.runtime.subprocess_runtime import SubprocessRuntime
from core.contracts import EventLog
from core.domain import ArtifactKind
from core.events import ArtifactAttached, Envelope, RuntimeExecuted, RuntimeFailed


class RuntimeAdapter:
    def __init__(
        self,
        event_log: EventLog,
        *,
        memory_limit_bytes: int | None = None,
        cpu_seconds: int | None = None,
    ) -> None:
        self.log = event_log
        resolved_memory_limit = memory_limit_bytes
        if resolved_memory_limit is None:
            resolved_memory_limit = int(
                os.environ.get("RUNTIME_MEMORY_BYTES", 512 * 1024 * 1024)
            )
        resolved_cpu_seconds = cpu_seconds
        if resolved_cpu_seconds is None:
            resolved_cpu_seconds = int(os.environ.get("RUNTIME_CPU_SECONDS", 10))

        self.runtime = SubprocessRuntime(
            memory_limit_bytes=resolved_memory_limit,
            cpu_seconds=resolved_cpu_seconds,
        )

    def execute(
        self,
        session_id: str,
        turn_id: str,
        code: str,
        timeout_seconds: float | None = None,
    ) -> str:
        result = self.runtime.execute(
            session_id,
            code,
            timeout_seconds=timeout_seconds,
        )
        seq = (self.log.last_seq(session_id) or 0) + 1
        now = datetime.now(UTC)

        if result.startswith("RUNTIME_OK"):
            wall_ms = self._parse_wall_ms(result)
            artifact_id = f"a-{uuid.uuid4().hex[:8]}"
            runtime_event = RuntimeExecuted(
                turn_id=turn_id,
                exit_code=0,
                wall_ms=wall_ms,
                artifact_id=artifact_id,
            )
            self.log.append(
                Envelope(
                    session_id=session_id,
                    seq=seq,
                    at=now,
                    payload=runtime_event,
                )
            )
            self.log.append(
                Envelope(
                    session_id=session_id,
                    seq=seq + 1,
                    at=datetime.now(UTC),
                    payload=ArtifactAttached(
                        id=artifact_id,
                        kind=ArtifactKind.cell_output,
                        produced_by_turn_id=turn_id,
                        version=1,
                    ),
                )
            )
            return artifact_id

        self.log.append(
            Envelope(
                session_id=session_id,
                seq=seq,
                at=now,
                payload=RuntimeFailed(
                    turn_id=turn_id,
                    reason=result,
                    wall_ms=self._parse_wall_ms(result),
                ),
            )
        )
        return ""

    @staticmethod
    def _parse_wall_ms(result: str) -> int:
        if "wall_ms=" not in result:
            return 0
        value = result.split("wall_ms=", 1)[1].split(";", 1)[0]
        try:
            return int(value)
        except ValueError:
            return 0
