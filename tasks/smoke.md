# Voice Smoke Test Checklist (η.8)

## Setup
- [ ] Start the server: `VOICE_MODE=on python -m uvicorn adapters.http.app:create_app --reload`
- [ ] Open Chrome dev tools (F12) for console inspection
- [ ] Navigate to `http://localhost:8000`
- [ ] Create a new session via the start page
- [ ] Verify the turn page loads without JavaScript errors

## Microphone & Permissions
- [ ] Verify browser requests microphone permission
- [ ] Grant microphone access when prompted
- [ ] Check console: should see "Voice interface initialized"

## Audio Capture & Streaming
- [ ] Speak one sentence (e.g., "Hello, this is a test")
- [ ] Verify partial transcript appears in `.voice-transcript` bubble within ~500ms
- [ ] Verify status chip changes from "Idle" → "Listening" during speech
- [ ] Check console: confirm `audio_chunk` messages logged with increasing `seq` numbers

## TTS Playback
- [ ] Verify server responds with voice (AI audio should play ~1.5s after speech ends)
- [ ] Status chip changes to "Speaking" during playback
- [ ] Audio plays through speaker (verify speaker is not muted)
- [ ] After audio finishes, status returns to "Idle"

## Voice Activity Detection (VAD) & Barge-In
- [ ] While AI is speaking, speak again (simulate barge-in)
- [ ] Verify AI audio stops mid-playback
- [ ] Verify status returns to "Listening" for next turn
- [ ] Console should show "Barge-in detected, stopping TTS"

## End-of-Speech Detection
- [ ] Speak one sentence and pause for ~800ms
- [ ] Verify transcript finalizes
- [ ] Verify `seq=-1` (end-of-stream sentinel) message sent to server
- [ ] Console should show "Silence detected, sending EOS"

## Text Panel Reveal (stage-dependent)
- [ ] If the next stage has `show_text_panel: true` (e.g., `verbal_whiteboard`):
  - [ ] `<details data-mode="text">` should automatically open
  - [ ] Textarea should be focused and label set to "Type your code/analysis here"
- [ ] If `show_text_panel: false`:
  - [ ] Details element remains closed
  - [ ] Voice bar remains visible

## Mode Switch (Voice → Text)
- [ ] Click "Switch to typing" button
- [ ] Verify WebSocket closes (console: "WebSocket closed")
- [ ] Verify `<details data-mode="text">` opens
- [ ] Verify text composer form is now visible and editable
- [ ] Type an answer and submit (should use text form submission)

## Backward Compatibility (voice_mode=off)
- [ ] Set `voice_mode: False` in `app.py`
- [ ] Reload page
- [ ] Verify voice bar is NOT visible
- [ ] Verify text composer form is visible and editable
- [ ] Verify all existing functionality works (send text/code answers)

## Error Handling
- [ ] Close microphone in system settings while page is open
- [ ] Verify status changes to "Error" (red badge)
- [ ] Check console for error message
- [ ] Page should remain functional (can navigate away)

## Network & WebSocket
- [ ] Open Network tab (DevTools → Network)
- [ ] Filter by WebSocket
- [ ] Verify `/ws/sessions/{id}/voice` connection is established
- [ ] Observe outbound `audio_chunk` frames (check Frame tab)
- [ ] Observe inbound `partial_transcript`, `tts_chunk`, `stage_change` frames

## Performance Notes
- [ ] Partial transcripts appear within ~500ms of user speech
- [ ] TTS playback begins within ~1.5s of server response
- [ ] No console errors or warnings (except expected microphone prompts)
- [ ] Page remains responsive during audio streaming
