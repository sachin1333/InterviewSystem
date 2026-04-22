---
title: "HEARTBEAT.md — Orchestrator Tick Rules"
---

# Heartbeat

The orchestrator ticks every N seconds (default: 2 — fast, because pacing is latency-critical) or immediately on any event. Each tick asks: "what action, if any, next?" This file is the checklist it consults.

## Checks, in order

1. **Candidate typing right now?** → `NoOp`. Never post while they type.
2. **Hard deadline exceeded?** → emit `SessionEnded`. Stop.
3. **Soft deadline exceeded by >10 min and candidate still active?** → one-time system nudge turn. Record the nudge.
4. **Is this the very first tick of the session?** → emit `AskExaminer(kind="greeting")`. Flag `warm=true`.
5. **30-minute mark passed and no break offered yet?** → emit `AskExaminer(kind="break_offer")`. Record it.
6. **Candidate silent >90 s since last turn and under soft deadline?** → emit a `nudge` turn (soft check-in). At most once per 5 minutes.
7. **Candidate just posted an answer?** →
   a. Schedule `RunScorers(turn_id)` **async** — never block.
   b. If answer is substantive (≥30 words or ≥2 lines of code), schedule `AskExaminer(kind="backchannel")` with a 200 ms delay (to avoid stepping on the last token).
   c. Schedule `AskExaminer(kind="probe", dimension=<weakest covered>)` with a minimum 800 ms floor after the backchannel.
8. **Candidate's last 3 answers short (<15 words) or very delayed (>60 s)?** → set `tone=soft` on the next probe request. (Examiner will dampen.)
9. **Rubric coverage ≥0.8 AND candidate has submitted?** → emit `AskExaminer(kind="closer")`, then `SessionEnded` after it posts.
10. **Otherwise** → `NoOp`.

## Pacing floor (hard rules)

- Minimum 800 ms between any two system-originated turns.
- Never two probes back-to-back without a candidate turn in between.
- Never interrupt a candidate who is typing — even for a backchannel.

## Scorer isolation

Scorers run **async**. `RunScorers` never blocks any `AskExaminer` action. If scorers are slow, the recruiter dashboard gets signals late. The candidate never sees it.

## Output contract

The orchestrator returns an `Action`, never an I/O side effect. One of:

- `AskChallenger(kind)`
- `AskExaminer(kind, tone=?, dimension=?)`
- `AwaitCandidate(timeout_s)`
- `RunScorers(turn_id)` — dispatched async
- `PostSystemTurn(text)`
- `End(reason)`
- `NoOp`

Actions are pure data. The caller executes them and posts resulting events.
