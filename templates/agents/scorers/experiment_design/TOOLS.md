---
title: "Experiment Design Scorer — TOOLS"
agent: scorer-experiment-design
---

# What you can call

- `session.turn(turn_id)` — fetch a specific turn with artifacts inlined.
- `session.artifact(artifact_id)` — fetch an artifact body (code, markdown, chart).

# Response contract

Return exactly one JSON object:

```json
{
  "signals": [
    {
      "dimension": "experiment_design",
      "value": 0.0,
      "confidence": 0.0,
      "source_refs": ["turn://...", "artifact://..."],
      "note": "..."
    }
  ]
}
```

**Value range:** `value` and `confidence` MUST each be a decimal number between 0.0 and 1.0 inclusive. Never return a value greater than 1.0 or less than 0.0. Do NOT return values like 4.0, 2.0, or strings like "weak" — these are invalid and will be rejected. Use 0.8 for "strong," 0.5 for "moderate," 0.2 for "weak."

If the turn contains no experiment or validation design content, return `{ "signals": [] }`. Do not invent signals.
