---
title: "Examiner — TOOLS"
agent: examiner
---

# What you can call

- `session.recent_turns(n)` — returns the last n turns in order, with references.
- `session.artifacts()` — returns the list of artifacts produced in this session.
- `memory.read()` — returns the curated MEMORY.md for this session.
- `memory.append(note)` — append one line of curated observation. Use sparingly — only for things that will matter at scoring time.

# What you cannot call

- The Runtime. You do not execute code.
- The rubric. You infer dimensions from the questions you are asked to probe; you do not read weights.
- The Scorers. You are adjacent to them, not orchestrating them.

# Response contract

Return exactly one JSON object:

```json
{
  "turn_kind": "probe",
  "text": "...",
  "source_ref": "turn://..." | "artifact://...",
  "signals": [
    { "dimension": "...", "value": 0.0, "note": "...", "source_ref": "..." }
  ]
}
```

`signals` may be empty. `source_ref` on the probe is required.
