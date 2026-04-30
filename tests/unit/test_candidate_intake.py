from __future__ import annotations

from core.candidate_intake import build_profile, render_user_md


def test_build_profile_scrubs_pii_and_extracts_candidate_context() -> None:
    profile = build_profile(
        candidate_handle="Ada Lovelace",
        declared_role="ML Engineer",
        years_experience="5",
        declared_skills_text="Python, PyTorch, SQL",
        resume_text="Ada Lovelace\nada@example.com\n+1 415 555 0101\nLed ranking model at ShopCo. Built churn pipeline.",
        other_details="Strongest in causal inference and experimentation.",
    )

    assert profile.declared_role == "ML Engineer"
    assert profile.years_experience == 5
    assert profile.declared_skills == ("Python", "PyTorch", "SQL")
    rendered = render_user_md(profile)
    assert "Ada Lovelace" not in rendered
    assert "ada@example.com" not in rendered
    assert "415 555 0101" not in rendered
    assert "Led ranking model at ShopCo" in rendered
    assert "Strongest in causal inference" in rendered


def test_render_user_md_leaves_absent_optional_fields_empty() -> None:
    profile = build_profile(
        candidate_handle="candidate",
        declared_role="",
        years_experience="not-a-number",
        declared_skills_text="",
        resume_text="",
        other_details="",
    )

    rendered = render_user_md(profile)

    assert "Role applied for:" in rendered
    assert "Years of experience" in rendered
    assert "No name, email, phone" in rendered
