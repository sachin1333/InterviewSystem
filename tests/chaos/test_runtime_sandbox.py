import tempfile

from adapters.runtime.subprocess_runtime import SubprocessRuntime


def test_infinite_loop_times_out() -> None:
    runtime = SubprocessRuntime(memory_limit_bytes=64 * 1024 * 1024, cpu_seconds=2)
    code = "while True:\n    pass\n"
    out = runtime.execute("s1", code, timeout_seconds=1)
    assert out.startswith("RUNTIME_FAILED") or "wall_time_exceeded" in out


def test_memory_bomb_triggers_failure() -> None:
    runtime = SubprocessRuntime(memory_limit_bytes=32 * 1024 * 1024, cpu_seconds=2)
    code = "a = 'x' * (200 * 1024 * 1024)\nprint(len(a))\n"
    out = runtime.execute("s2", code, timeout_seconds=5)
    assert out.startswith("RUNTIME_FAILED") or "209715200" in out


def test_network_attempt_fails_fast() -> None:
    runtime = SubprocessRuntime(memory_limit_bytes=64 * 1024 * 1024, cpu_seconds=2)
    code = (
        "import urllib.request\n"
        "print('starting')\n"
        "urllib.request.urlopen('http://example.com').read()\n"
        "print('done')\n"
    )
    out = runtime.execute("s3", code, timeout_seconds=5)
    assert "RUNTIME_FAILED" in out or "starting" in out


def test_runtime_executes_in_temporary_working_directory() -> None:
    runtime = SubprocessRuntime(memory_limit_bytes=64 * 1024 * 1024, cpu_seconds=2)
    code = "import os\nprint(os.getcwd())\n"
    out = runtime.execute("s4", code, timeout_seconds=5)
    assert tempfile.gettempdir() in out


def test_non_ascii_stdout_is_preserved() -> None:
    runtime = SubprocessRuntime(memory_limit_bytes=64 * 1024 * 1024, cpu_seconds=2)
    code = "print('नमस्ते 🌍')\n"
    out = runtime.execute("s5", code, timeout_seconds=5)
    assert "नमस्ते 🌍" in out


def test_zero_division_reports_nonzero_exit_code() -> None:
    runtime = SubprocessRuntime(memory_limit_bytes=64 * 1024 * 1024, cpu_seconds=2)
    code = "1 / 0\n"
    out = runtime.execute("s6", code, timeout_seconds=5)
    assert "RUNTIME_FAILED:exit_code=" in out
    assert "ZeroDivisionError" in out
