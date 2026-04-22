---
title: "CandidateIntake — TOOLS"
agent: candidate-intake
model_tier: fast
---

# What you can call

- `profile_source.fetch(name, handle)` — invokes a registered `ProfileSource` adapter. Returns a `ProfileFragment`.
  - MVP names: `"resume"`, `"linkedin"`, `"blog"`, `"github"`.
  - Only sources the candidate consented to are wired in. Others return `null`.
- `redactor.scrub(text)` — runs the PII redaction pass. Use on every raw string before you store or forward it.
- `artifacts.attach(kind, payload)` — stores the raw source under the session's `artifacts/` directory, audit-only, never read by agents.

# What you cannot call

- An LLM with more than one pass. You are a fast, structured extractor. If you find yourself thinking hard, you are doing the wrong job — the Examiner and Scorers do the thinking.
- Any source the candidate did not consent to.
- The public internet beyond registered sources. No ad-hoc scraping.

# Response contract

Emit exactly one `ProfileIngested` event:

```json
{
  "event": "ProfileIngested",
  "session_id": "...",
  "profile": {
    "declared_role": "...",
    "years_experience": 0,
    "declared_skills": ["..."],
    "projects": [
      { "name": "...", "one_liner": "...", "source": "resume|linkedin|github|blog" }
    ],
    "publications": [
      { "title": "...", "url": "...", "source": "..." }
    ],
    "claims": [
      { "text": "...", "source": "...", "confidence": 0.0 }
    ]
  }
}
```

Then render `USER.md` from `templates/session/USER.md.tmpl` with these fields and write it to the session workspace. Done.
