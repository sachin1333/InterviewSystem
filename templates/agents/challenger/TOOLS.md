---
title: "Challenger — TOOLS"
agent: challenger
---

# What you can call

- `rubric.dimensions()` — returns the list of dimension names (not weights, not descriptions). Use it only to check coverage, not to shape the prompt in rubric language.
- `artifacts.attach(kind, payload)` — attach a starter notebook or dataset URI to the turn.

# What you cannot call

- The LLM for anything outside of composing this turn.
- The Runtime. You never execute code yourself.
- The Examiner or Scorers. No sidechannel communication.

# Response contract

Return exactly one JSON object:

```json
{
  "turn_kind": "question",
  "prompt_markdown": "...",
  "artifact_refs": ["artifact://..."],
  "soft_deadline_minutes": 45
}
```

No extra keys. No commentary outside the JSON.
