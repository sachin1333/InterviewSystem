from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


@dataclass(frozen=True)
class CandidateProfile:
    declared_role: str
    years_experience: int | None
    declared_skills: tuple[str, ...]
    claims: tuple[str, ...]
    projects: tuple[str, ...] = ()


def scrub_pii(text: str, *, candidate_handle: str = "") -> str:
    """Best-effort local PII scrub for the MVP intake text path."""
    cleaned = _EMAIL_RE.sub("[redacted-email]", text)
    cleaned = _PHONE_RE.sub("[redacted-phone]", cleaned)
    handle = candidate_handle.strip()
    if handle and handle.lower() != "candidate":
        cleaned = re.sub(re.escape(handle), "[redacted-name]", cleaned, flags=re.I)
    return cleaned.strip()


def build_profile(
    *,
    candidate_handle: str,
    declared_role: str,
    years_experience: str,
    declared_skills_text: str,
    resume_text: str,
    other_details: str,
) -> CandidateProfile:
    skills = tuple(
        dict.fromkeys(
            skill.strip()
            for chunk in declared_skills_text.splitlines()
            for skill in chunk.split(",")
            if skill.strip()
        )
    )
    combined = "\n".join([resume_text, other_details])
    scrubbed_lines = [
        line.strip(" -•\t")
        for line in scrub_pii(combined, candidate_handle=candidate_handle).splitlines()
        if line.strip(" -•\t")
        and line.strip(" -•\t") not in {"[redacted-email]", "[redacted-phone]"}
    ]
    return CandidateProfile(
        declared_role=declared_role.strip() or "DS / ML Engineer",
        years_experience=_parse_years(years_experience),
        declared_skills=skills,
        claims=tuple(scrubbed_lines[:12]),
    )


def render_user_md(profile: CandidateProfile) -> str:
    years = "unknown" if profile.years_experience is None else str(profile.years_experience)
    skills = _bullet_list(profile.declared_skills)
    claims = _bullet_list(profile.claims, quoted=True)
    projects = _bullet_list(profile.projects)
    return "\n".join([
        "# Candidate",
        "",
        f"- **Role applied for:** {profile.declared_role}",
        f"- **Years of experience (self-reported):** {years}",
        "",
        "_No name, email, phone, address, photo, or demographic signal is loaded into this file._",
        "",
        "## Declared skills",
        skills,
        "",
        "## Projects (from profile)",
        projects,
        "",
        "## Claims from profile",
        claims,
        "",
    ])


def _parse_years(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


def _bullet_list(items: tuple[str, ...], *, quoted: bool = False) -> str:
    if not items:
        return "- _(none provided)_"
    if quoted:
        return "\n".join(f'- "{item}"' for item in items)
    return "\n".join(f"- {item}" for item in items)
