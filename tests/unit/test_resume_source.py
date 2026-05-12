from __future__ import annotations

from pathlib import Path

import pytest

from core.profile_sources import ProfileSourceError, ResumeSource


def test_resume_source_extracts_txt_resume() -> None:
    fragment = ResumeSource(max_bytes=1024).fetch_bytes("resume.txt", b"Python\nLed ranking model")
    assert fragment.source == "resume"
    assert "Led ranking model" in fragment.text
    assert fragment.raw_excerpt_hash


def test_resume_source_rejects_large_file() -> None:
    with pytest.raises(ProfileSourceError, match="too large"):
        ResumeSource(max_bytes=3).fetch_bytes("resume.txt", b"abcd")


def test_resume_source_rejects_unknown_extension() -> None:
    with pytest.raises(ProfileSourceError, match="unsupported"):
        ResumeSource().fetch_bytes("resume.exe", b"nope")


def test_project_declares_pdf_resume_parser_runtime_dependency() -> None:
    import tomllib

    project = tomllib.loads(Path("pyproject.toml").read_text())
    dependencies = {dep.split(">=", 1)[0].lower().replace("_", "-") for dep in project["project"]["dependencies"]}

    assert "pdfminer-six" in dependencies
