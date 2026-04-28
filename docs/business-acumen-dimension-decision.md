# Phase 2.6.4 Business-Acumen Dimension Decision

Date: 2026-04-28

## Decision

Keep business judgment inside `insight_interp` for Phase 2. Do not split or rename the rubric dimension yet.

## Rationale

Phase 2 uses short, multi-problem interviews where business judgment is usually observable through interpretation choices: metric trade-offs, stakeholder impact, experiment readouts, and communication of uncertainty. Splitting a new dimension now would dilute sparse evidence across too many dimensions and destabilize comparability with Phase alpha session-level scores.

## Ten-scenario check

| # | Scenario | Primary signal observed | Covered by `insight_interp`? | Note |
|---:|---|---|---|---|
| 1 | Candidate chooses marketplace guardrail metrics for A/B test | Connects metric to business risk | Yes | Classic interpretation judgment |
| 2 | Candidate explains why AUC hides segment harm | Converts model metric to operational impact | Yes | Requires business-aware metric reading |
| 3 | Candidate pushes back on optimizing only revenue | Balances short-term KPI vs trust | Yes | Fits insight interpretation + communication |
| 4 | Candidate prioritizes data-quality fix before modeling | Frames downstream decision risk | Partly | Also `problem_framing` |
| 5 | Candidate interprets null experiment result | Separates statistical vs practical significance | Yes | Strong `insight_interp` evidence |
| 6 | Candidate picks interpretable baseline for regulated domain | Maps model choice to deployment constraints | Partly | Also `model_rationale` |
| 7 | Candidate recommends launch rollback after metric regression | Turns evidence into business action | Yes | Direct business judgment |
| 8 | Candidate cannot quantify trade-off but communicates caveat | Stakeholder-facing uncertainty | Partly | Also `communication` |
| 9 | Candidate spots sample bias in executive dashboard | Explains decision risk from skew | Yes | Interpretation of evidence quality |
| 10 | Candidate proposes phased rollout for risky model | Operationalizes insight into safer decision | Yes | Captured by interpretation + communication |

## Follow-up trigger

Revisit the split only if recruiter review finds business judgment missing in at least 25% of sessions where written feedback mentions stakeholder or business trade-offs. Until then, keep the rubric stable and improve scorer prompts/examples for `insight_interp` rather than adding a dimension.
