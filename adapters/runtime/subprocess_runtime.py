from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path


class SubprocessRuntime:
    """Execute candidate Python in a short-lived subprocess with best-effort caps."""

    def __init__(
        self,
        *,
        memory_limit_bytes: int = 512 * 1024 * 1024,
        cpu_seconds: int = 10,
    ) -> None:
        self.memory_limit = memory_limit_bytes
        self.cpu_seconds = cpu_seconds

    def _preexec_fn(self) -> None:
        try:
            import resource
        except Exception:
            return

        with suppress(Exception):
            resource.setrlimit(
                resource.RLIMIT_AS,
                (self.memory_limit, self.memory_limit),
            )
        with suppress(Exception):
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (self.cpu_seconds, self.cpu_seconds + 1),
            )
        with suppress(Exception):
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 64))

    def execute(
        self,
        session_id: str,
        code: str,
        timeout_seconds: float | None = None,
    ) -> str:
        del session_id
        with tempfile.TemporaryDirectory(prefix="interview-runtime-") as temp_dir:
            script_path = Path(temp_dir) / "candidate.py"
            script_path.write_text(code, encoding="utf8")

            env = os.environ.copy()
            env.update(
                {
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                    "ALL_PROXY": "http://127.0.0.1:1",
                }
            )

            start = time.monotonic()
            try:
                proc = subprocess.run(
                    [sys.executable, "-u", str(script_path)],
                    capture_output=True,
                    cwd=temp_dir,
                    env=env,
                    timeout=timeout_seconds,
                    preexec_fn=self._preexec_fn if hasattr(os, "fork") else None,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return "RUNTIME_FAILED:wall_time_exceeded"

            wall_ms = int((time.monotonic() - start) * 1000)
            stdout = proc.stdout.decode("utf8", "surrogateescape")
            stderr = proc.stderr.decode("utf8", "surrogateescape")

            if proc.returncode != 0:
                return (
                    f"RUNTIME_FAILED:exit_code={proc.returncode};"
                    f"wall_ms={wall_ms};stderr={stderr};stdout={stdout}"
                )

            return f"RUNTIME_OK:exit_code=0;wall_ms={wall_ms};stdout={stdout}"
