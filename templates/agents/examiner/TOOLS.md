---
title: "Examiner — TOOLS"
agent: examiner
phase: "2.2+"
---

# Context injected into your prompt

Before your response, the system injects:

- **`transcript`** — full conversation for the *active problem only* (not the full session).
- **`under_served_dims`** — list of rubric dimensions still below their signal threshold.
- **`signal_map`** — current accumulated signal per dimension (0.0–1.0).
- **`probe_count`** — number of probes already issued for this problem.
- **`max_probes`** — hard cap; at or above this you MUST emit `close`.

# Response contract

Return **exactly one** JSON object — no markdown, no commentary:

```json
{
  "action": "probe" | "close",
  "text": "...",
  "reason": "coverage_saturated" | "time_capped" | "examiner_pivot" | "max_probes",
  "rationale": "...",
  "primitive_hint": "think_aloud" | "socratic_rebuttal" | "counterfactual" | "resume_deep_dive" | "verbal_whiteboard" | "one_bullet"
}
```

Field rules:

| Field | Required for | Notes |
|---|---|---|
| `action` | always | `"probe"` or `"close"` |
| `text` | `probe` | The question text shown to the candidate. 1–3 sentences. |
| `reason` | `close` | Why you are closing. |
| `rationale` | always | Internal note for audit log. Not shown to candidate. 1 sentence. |
| `primitive_hint` | optional | Hint to renderer about probe style. |

# What you cannot do

- Execute code.
- Read rubric weights.
- Invoke scorers.
- Produce any output other than the single JSON object above.
