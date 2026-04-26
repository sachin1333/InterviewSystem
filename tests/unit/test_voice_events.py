from core.domain import ArtifactKind, TurnKind
from core.events import (
    EVENT_TYPES,
    AudioChunkAttached,
    LatencyObserved,
    SpeechFinalized,
    SpeechStarted,
)


def test_voice_turn_kinds_present() -> None:
    assert TurnKind.spoken_question.value == "spoken_question"
    assert TurnKind.spoken_answer.value == "spoken_answer"
    assert TurnKind.spoken_probe.value == "spoken_probe"


def test_voice_artifact_kinds_present() -> None:
    assert ArtifactKind.transcript.value == "transcript"
    assert ArtifactKind.audio_ref.value == "audio_ref"  # already exists


def test_speech_lifecycle_events_registered() -> None:
    for evt in (SpeechStarted, SpeechFinalized, AudioChunkAttached, LatencyObserved):
        assert evt in EVENT_TYPES


def test_speech_finalized_carries_transcript_and_timing() -> None:
    evt = SpeechFinalized(
        turn_id="t-x",
        transcript="hello world",
        wpm=170,
        first_partial_ms=120,
        final_ms=850,
        filler_count=2,
    )
    assert evt.transcript == "hello world"
    assert evt.first_partial_ms == 120
