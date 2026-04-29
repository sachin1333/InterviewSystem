"""WebSocket protocol messages for voice interview streaming.

Messages are JSON objects exchanged bidirectionally between client and server.

Client messages:
  - audio_chunk: PCM audio data from candidate
  - mode_switch: Switch between voice and text input (ends voice stream)

Server messages:
  - partial_transcript: STT output (before examiner response)
  - examiner_text: examiner response text (sent even when TTS is disabled)
  - tts_chunk: TTS audio chunks of the examiner's response
  - stage_change: Transition to next interview stage

End-of-stream signal:
  The client sends an audio_chunk with seq=-1 and empty pcm_b64 to signal end of speech.
  The server sends tts_chunk with end_of_utterance=True to signal final audio chunk.
"""
from typing import Literal, TypedDict


class ClientAudioChunk(TypedDict):
    """PCM audio data from candidate microphone.

    seq: Strictly increasing sequence number (0, 1, 2, ...).
         Special value -1 with empty pcm_b64 signals end-of-stream.
    pcm_b64: Base64-encoded raw PCM bytes (16-bit signed, 16kHz).
    """
    type: Literal["audio_chunk"]
    seq: int
    pcm_b64: str


class ClientModeSwitch(TypedDict):
    """Candidate switches from voice to typed input (or vice versa).

    When mode="text", the server cancels the current voice turn and
    gracefully closes the WebSocket (code 1000).
    """
    type: Literal["mode_switch"]
    mode: Literal["voice", "text"]


class ServerPartialTranscript(TypedDict):
    """Candidate's speech-to-text transcript (sent before audio response).

    is_final: True when STT has finalized (after speech ends);
              False for partial/streaming updates.
    """
    type: Literal["partial_transcript"]
    text: str
    is_final: bool


class ServerExaminerText(TypedDict):
    """Examiner response text, independent of text-to-speech audio.

    Sent after the final candidate transcript so clients can render live
    interviewer text even when TTS_MODE=off or audio playback is unavailable.
    """
    type: Literal["examiner_text"]
    text: str


class ServerTtsChunk(TypedDict):
    """Examiner's text-to-speech audio chunk (streamed over HTTP).

    pcm_b64: Base64-encoded raw PCM bytes (16-bit signed, 16kHz).
    end_of_utterance: True on the final chunk to signal end of response.
    """
    type: Literal["tts_chunk"]
    pcm_b64: str
    end_of_utterance: bool


class ServerStageChange(TypedDict):
    """Transition to next interview stage after examiner's response completes.

    case_stage: Stage ID (e.g., "problem_framing", "methodology").
    primitive: Primitive enum value (e.g., "think_aloud", "socratic_rebuttal").
    show_text_panel: True if the current primitive expects typed input
                     (e.g., for code/SQL whiteboarding); False for voice-only.
    """
    type: Literal["stage_change"]
    case_stage: str
    primitive: str
    show_text_panel: bool
