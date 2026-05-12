---
title: "Problem Framing Scorer — TOOLS"
agent: scorer-problem-framing
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
      "dimension": "problem_framing",
      "value": 0.0,
      "confidence": 0.0,
      "source_refs": ["turn://...", "artifact://..."],
      "note": "..."
    }
  ]
}
```

**Value range:** `value` and `confidence` MUST each be a decimal number between 0.0 and 1.0 inclusive. Never return a value greater than 1.0 or less than 0.0.

If the turn contains no problem framing content (pure code execution, no reasoning), return `{ "signals": [] }`. Do not invent signals.
