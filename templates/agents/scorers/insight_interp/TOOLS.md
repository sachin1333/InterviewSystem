---
title: "Insight Interpretation Scorer — TOOLS"
agent: scorer-insight-interp
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
      "dimension": "insight_interp",
      "value": 0.0,
      "confidence": 0.0,
      "source_refs": ["turn://...", "artifact://..."],
      "note": "..."
    }
  ]
}
```

**Value range:** `value` and `confidence` MUST each be a decimal number between 0.0 and 1.0 inclusive. Never return a value greater than 1.0 or less than 0.0. A value of 0.8 means "strong signal present." A value of 1.0 means "exceptional, textbook example."

If the turn contains no interpretive content (pure code execution, no narrative, no conclusion drawn), return `{ "signals": [] }`. Do not invent signals.
