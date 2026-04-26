# Voice-First Interview — Research Findings (2026-04-26)

> Source-of-truth doc captured during planning of Phase η. Subsequent η-tasks (η.1–η.13) reference these decisions; do not contradict them without updating this file.

## TL;DR

- **Stack**: Wispr Flow (WebSocket STT, 16kHz PCM), Cartesia Sonic TTS primary (~90ms first byte), ElevenLabs Flash fallback (~75ms). OpenRouter SSE for LLM streaming.
- **Latency budget (p50)**: 800ms total voice-to-response; p95 target 1500ms. VAD+capture 50ms, STT partial 150ms, LLM TTFT 400ms, TTS first byte 100ms, network 100ms.
- **Six question primitives**: think_aloud, socratic_rebuttal, counterfactual, resume_deep_dive, verbal_whiteboard, one_bullet—each targets specific reasoning depth and cheating-defense signals.
- **Multi-stage case**: 5 stages over ~13–20 min (problem_framing → methodology → execution → interpretation → synthesis); source patterns from PhD viva and OSCE.
- **Cheating-defense approach**: Cluster of 3+ flags (timing signature, counterfactual freeze, resume personalization, casual pivot, confidence mismatch, recognition-vs-recall asymmetry) raises suspicion; no single signal determinative.

## 1. Stack decision

**Speech-to-Text (STT)**: Wispr Flow
- WebSocket API (recommended for streaming)
- API endpoint pattern: `wss://platform-api.wisprflow.ai/api/v1/dash/ws?api_key=Bearer <API_KEY>`
- Audio input: base64-encoded single-channel 16-bit PCM WAV at 16kHz
- Output: streamed partial + final transcription results
- Auth: first message must include auth type and access token
- STT only (no TTS capability)

**Text-to-Speech (TTS) — Primary**: Cartesia Sonic
- Model: `sonic-english`
- Endpoint: `https://api.cartesia.ai/tts/sse`
- First-byte latency: ~90ms

**Text-to-Speech (TTS) — Fallback**: ElevenLabs Flash
- Model: `eleven_flash_v2_5`
- Endpoint pattern: `https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream`
- First-byte latency: ~75ms

**Language Model (LLM)**: OpenRouter SSE
- Streaming via Server-Sent Events
- Target: sub-second TTFT (time-to-first-token)

## 2. Latency budget

**p50 target: 800ms** (voice-to-response, single roundtrip)

| Stage | Budget (ms) |
|---|---|
| VAD + audio capture | 50 |
| STT first partial | 150 |
| LLM TTFT | 400 |
| TTS first byte | 100 |
| Network roundtrip | 100 |
| **Total** | **800** |

**p95 target: 1500ms**

**Industry context (2026 benchmarks)**:
- OpenAI Realtime API: ~200–300ms voice-to-voice on local network (WebRTC); ~300–450ms over WebSocket; TTFT ~500ms
- Vapi: claims ~465ms in optimal conditions
- Production median: 1.4–1.7s for typical voice agents

**Perception thresholds**:
- Pauses >300ms feel unnatural
- Pauses >1.5s degrade experience significantly per industry research

## 3. Question primitives

Six standardized primitives for voice-first interviewing:

| Primitive | Trigger | Duration | Primary tests | Cheating defense? |
|---|---|---|---|---|
| **think_aloud** | Session start, problem posed | 90–120s | Problem framing, insight interpretation, decomposition | No |
| **socratic_rebuttal** | After candidate states a method/choice | 60–90s | Reasoning depth, trade-off awareness, willingness to pivot | Yes |
| **counterfactual** | Mid-case, after candidate commits to approach | 45–90s | Adaptability, robustness to unseen scenarios | Yes |
| **resume_deep_dive** | Early in case, personalized to CV claims | 60–120s | Authenticity (proxy fraud), depth of stated experience | Yes |
| **verbal_whiteboard** | System design / pipeline questions | 120–180s | Architecture clarity, multi-stage process holding | No |
| **one_bullet** | Mid-case or end-of-case | 15–30s | Prioritization, ruthless clarity | No |

## 4. Multi-stage case shape

**Standard case flow (5 stages, ~13–20 min total)**:

1. **problem_framing** (~120s)
   - Question type: think_aloud
   - Task: restate business problem, decompose into subproblems

2. **methodology** (~75s)
   - Question type: socratic_rebuttal
   - Task: defend chosen approach

3. **execution** (~150s)
   - Question type: verbal_whiteboard
   - Task: walk validation strategy step by step

4. **interpretation** (~60s)
   - Question type: counterfactual
   - Task: handle "assume metric X dropped Y%" pivot

5. **synthesis** (~25s)
   - Question type: one_bullet
   - Task: explain to non-technical exec in one sentence

**Source patterns**: PhD viva voce, OSCE (Objective Structured Clinical Examinations), think-aloud protocol

## 5. Cheating-defense signals

**Approach**: Cluster of 3+ flags raises suspicion; no single signal is determinative.

### Individual signals

- **Timing-signature trapdoor**: AI-assisted answers have suspiciously consistent latency. Compare response times on easy questions vs hard questions—if identical, flag.

- **Counterfactual adaptation trapdoor**: AI-coached candidates freeze, repeat, or visibly delay when a "what if X changed" probe disrupts cached reasoning.

- **Resume personalization trapdoor**: Ask for specific numbers (class imbalance ratios, dataset shapes, metrics) about a stated prior project. Generic answers = proxy fraud or AI helper.

- **Casual-pivot trapdoor**: Alternate technical questions and conversational ones. AI-coached candidates show delays when shifting register.

- **Confidence-uncertainty mismatch**: Polished confidence on edge-case questions is suspicious. Genuine candidates use natural hesitation markers ("um", "I'm not sure but…").

- **Recognition-vs-recall asymmetry**: Ask candidate to verbally explain a concept (recall) and to recognize it from a description (recognition). AI-coached candidates often memorize definitions but fail at bidirectional application.

## 6. Wispr Flow API specifics (April 2026)

- **STT only**: Wispr Flow provides speech-to-text only; pair with Cartesia or ElevenLabs for TTS.
- **WebSocket API**: Recommended for streaming. Auth via API-key or client-side auth; first message must include auth type + access token.
- **Audio input**: base64-encoded single-channel 16-bit PCM WAV at 16kHz.
- **Output**: streamed partial + final transcription results.
- **Enterprise plan**: Supports unlimited interactions, full API access, white-labeling, custom integrations.
- **Pairing recommendation**: Wispr Flow STT + Cartesia Sonic TTS (or ElevenLabs Flash as fallback).

## 7. Question taxonomy → scoring signal map

Each primitive is associated with the scoring signals it most strongly produces:

- **think_aloud** → problem_framing, insight_interp
- **socratic_rebuttal** → model_rationale, communication
- **counterfactual** → model_rationale, problem_framing + AI cheating detection
- **resume_deep_dive** → problem_framing + proxy fraud detection
- **verbal_whiteboard** → experiment_design (or communication-heavy variant), communication
- **one_bullet** → communication

**Sequencing recommendation** (60–90 min full session, or single deep case MVP):
think_aloud → socratic_rebuttal → counterfactual → resume_deep_dive → verbal_whiteboard → one_bullet

## Sources

- https://www.businesswire.com/news/home/20251210685922/en/Karat-Launches-NextGen-Interviews-The-First-Human-Led-AI-Enabled-Talent-Evaluation-Solution
- https://hirevue.com/
- https://www.talview.com/en/stop-parakeet-ai-cheating
- https://karat.com/detect-ai-use-technical-interviews/
- https://smallest.ai/blog/openai-real-time-api-complete-breakdown
- https://deepgram.com/learn/best-text-to-speech-apis-2026
- https://dev.to/tigranbs/sub-second-voice-agent-latency-a-practical-architecture-guide-4cg1
- https://pmc.ncbi.nlm.nih.gov/articles/PMC3191703/
- https://eloquentscience.com/2024/06/top-40-potential-questions-to-be-asked-in-a-phd-viva-or-defense
- https://www.tandfonline.com/doi/full/10.1080/26939169.2022.2063209
- https://wisprflow.ai/
- https://api-docs.wisprflow.ai/websocket_api
- https://www.interviewquery.com/p/data-science-case-study-interview-questions
- https://introl.com/blog/voice-ai-infrastructure-real-time-speech-agents-asr-tts-guide-2025
- https://hirevire.com/blog/best-digital-interview-platforms
