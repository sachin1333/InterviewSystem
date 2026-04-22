---
title: "BOOTSTRAP.md — Authoring a New Interview Template"
run_when: "A hiring manager or SME is defining a new interview template (role + rubric + persona overrides)."
---

# Bootstrap — New Interview Template

Run this once per new template (e.g. "Senior ML Engineer, Q2 2026"). After first run, delete the generated `BOOTSTRAP_COMPLETE` sentinel and never re-run unless the template itself is being replaced.

## Steps

1. **Name the template.**
   - `template.name`, `template.version`, `template.role`.

2. **Define the rubric.**
   - Copy `templates/rubrics/ds-ml-engineer-v1.yaml` → `templates/rubrics/<template-slug>.yaml`.
   - Edit dimensions, weights, and per-dimension description. Weights sum to 1.0.

3. **Pick or override agent personas.**
   - For each of Challenger, Examiner, Scorers:
     - Default: use `templates/agents/<agent>/`.
     - Override: copy that folder to `templates/instances/<template-slug>/agents/<agent>/` and edit `IDENTITY.md` / `SOUL.md`.
   - `TOOLS.md` should rarely be overridden — it encodes the contract, not the personality.

4. **Write the opening challenge seed.**
   - `templates/instances/<template-slug>/seed-challenge.md` — the reference case study the Challenger should riff on.
   - Do **not** write the exact prompt; the Challenger composes it per session.

5. **Attach a starter dataset (optional).**
   - Drop into `templates/instances/<template-slug>/datasets/`. Reference by URI in the seed.

6. **Smoke test.**
   - Run `python -m interview_system.bootstrap.smoke --template <slug>`.
   - It starts a simulated session with a stub candidate and prints:
     - Whether every rubric dimension got at least one signal from a scorer.
     - Whether the Examiner probed at least once.
     - Whether the session ended cleanly.
   - Any red → fix before shipping the template.

7. **Mark complete.**
   - `touch templates/instances/<template-slug>/BOOTSTRAP_COMPLETE`.
   - Commit.

## After bootstrap

The authoring UX is done. Every subsequent session uses `BOOT.md.tmpl` + this template to materialize a per-session workspace — no more authoring needed.
