import os
import subprocess
import sys


def test_run_interview_fake_llm_invokes_challenger():
    env = os.environ.copy()
    env["FAKE_LLM"] = "1"
    # run the module via -m to ensure package imports resolve
    res = subprocess.run([sys.executable, "-m", "tools.run_interview"], capture_output=True, text=True, env=env)
    out = res.stdout + res.stderr
    assert res.returncode == 0, f"process failed: {out}"
    assert "Proposed prompts" in out
    assert "Generated prompt" in out
