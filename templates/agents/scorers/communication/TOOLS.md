---
title: "Communication Scorer — TOOLS"
agent: scorer-communication
---

# What you can call

- `session.turn(turn_id)`
- `session.artifact(artifact_id)` — markdown cells, text summaries, transcript turns.

# Response contract

Return exactly one JSON object:

```json
{
  "signals": [
    {
      "dimension": "communication",
      "value": 0.0,
      "confidence": 0.0,
      "source_refs": ["artifact://..."],
      "note": "..."
    }
  ]
}
```

**Value range:** `value` and `confidence` MUST each be a decimal number between 0.0 and 1.0 inclusive. Never return a value greater than 1.0 or less than 0.0.

If the turn contains no explanatory content (pure code, no markdown, no summary), return `{ "signals": [] }`.
