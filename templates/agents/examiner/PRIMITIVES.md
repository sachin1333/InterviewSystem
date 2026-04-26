# Question Primitives — Examiner Reference

## Overview

This guide describes the six question primitives used in the multi-stage case interview. Each primitive targets specific evaluation dimensions and occupies a distinct position in the interview flow.

## Primitive Definitions

### think_aloud
Force candidate to externalize first-five-steps reasoning out loud.
- **Duration**: 90–120 seconds
- **Primary Signals**: problem_framing, insight_interp
- **Position in Case**: problem_framing (stage 1)
- **Guidance**: Open with this to establish how the candidate breaks down ambiguous business problems. Listen for structure, prioritization, and ability to identify hidden constraints.

### socratic_rebuttal
Challenge a stated method choice; require defense or pivot.
- **Duration**: 60–90 seconds
- **Primary Signals**: model_rationale, communication
- **Position in Case**: methodology (stage 2)
- **Guidance**: After the candidate proposes a modeling approach, push back. Do they defend with evidence or data? Can they pivot gracefully? This reveals real-time reasoning vs. memorized scripts.

### verbal_whiteboard
Describe a multi-stage pipeline (e.g., retraining loop) in words only.
- **Duration**: 120–180 seconds
- **Primary Signals**: experiment_design, communication
- **Position in Case**: execution (stage 3)
- **Guidance**: Ask the candidate to walk through a multi-step validation or retraining system without whiteboarding. Assess systems thinking, ability to sequence steps, and clarity of explanation.

### counterfactual
Inject 'assume X changed' to disrupt cached reasoning.
- **Duration**: 45–90 seconds
- **Primary Signals**: model_rationale, problem_framing
- **Position in Case**: interpretation (stage 4)
- **Guidance**: Shift a key assumption (e.g., "precision drops 20%"). Watch whether the candidate can re-evaluate their approach or if they're locked into a single frame.

### resume_deep_dive
Ask for class imbalance ratios / dataset shapes from a stated prior project.
- **Duration**: 60–120 seconds
- **Primary Signals**: problem_framing
- **Guidance**: When a candidate mentions prior experience, drill into specifics (dataset size, class distributions, etc.). Detects whether they can recall and articulate concrete project details or are speaking in abstractions.

### one_bullet
One sentence: why does this metric matter?
- **Duration**: 15–30 seconds
- **Primary Signals**: communication
- **Position in Case**: synthesis (stage 5)
- **Guidance**: Final probe. Ask the candidate to distill one insight into a single sentence suitable for a non-technical executive. Tests whether they can move from technical depth to executive summary.

---

## Cheating-Defense Primitives

Three primitives are explicitly designed to resist scripted or memorized answers:
- **socratic_rebuttal** — Requires real-time defense; adversarial follow-up reveals depth
- **counterfactual** — Assumption shifts break cached reasoning patterns
- **resume_deep_dive** — Spot-checks concrete project details that cannot be easily memorized

---

## Five-Stage Case Flow

1. **problem_framing** (think_aloud) → Establish baseline mental model
2. **methodology** (socratic_rebuttal) → Challenge the approach
3. **execution** (verbal_whiteboard) → Assess systems thinking
4. **interpretation** (counterfactual) → Test flexibility
5. **synthesis** (one_bullet) → Validate communication clarity
