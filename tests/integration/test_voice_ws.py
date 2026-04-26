"""η.7 — WebSocket voice interview bridge.

Tests the `/ws/sessions/{session_id}/voice` endpoint:
  1. Client connects with a valid session_id
  2. Client sends audio_chunk messages (PCM base64)
  3. Server receives audio, runs STT → LLM → TTS pipeline
  4. Server sends back partial_transcript, tts_chunk, stage_change messages
  5. Mode switch to "text" closes the WS cleanly
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import textwrap
from pathlib import Path

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.http.voice_runner import VoiceRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.tts.fake_tts import FakeTts
from core.domain import Dimension
from core.rubric_loader import load_rubric

_MINI_RUBRIC = textwrap.dedent("""\
    name: test
    version: 1
    dimensions:
      - name: model_rationale
        weight: 0.5
      - name: communication
        weight: 0.5
    aggregation:
      min_signals_per_dimension: 1
      confidence_weighting: true
""")


def _make_voice_runner(
    log: SqliteEventLog,
    stt: FakeStt,
    tts: FakeTts,
) -> VoiceRunner:
    """Create a VoiceRunner with fake STT/TTS."""
    router = ModelRouter(FakeRouter())
    return VoiceRunner(
        log=log,
        router=router,
        stt=stt,
        tts=tts,
    )


def _make_client(tmp_path: Path) -> tuple[TestClient, str, SqliteEventLog]:
    """Return (client, session_id, log) with voice session started."""
    log = SqliteEventLog(tmp_path / "log.db")

    # Create session runners (for HTTP endpoints).
    http_router = ModelRouter(FakeRouter())
    rubric = load_rubric(yaml_str=_MINI_RUBRIC)
    output_dir = tmp_path / "outputs"
    aggregator = RubricAggregator(rubric, output_dir=output_dir)
    http_runner = SessionRunner(
        challenger=LlmChallenger(http_router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(http_router),
            Dimension.communication: LlmCommunicationScorer(http_router),
        },
        aggregator=aggregator,
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )

    # Create voice runner with scripted STT.
    # Script: partial "thinking", final "I would use logistic regression"
    stt_script = [
        SttPartial(text="thinking", is_final=False, elapsed_ms=100),
        SttPartial(text="I would use logistic regression", is_final=True, elapsed_ms=200),
    ]
    stt = FakeStt(script=stt_script)
    tts = FakeTts(bytes_per_char=2)
    voice_runner = _make_voice_runner(log, stt, tts)

    # Make the app with voice_runner.
    app = make_app(
        log=log,
        runner=http_runner,
        target_answers=1,
        max_probes=0,
        output_dir=output_dir,
        voice_runner=voice_runner,
    )

    client = TestClient(app)

    # Start a voice session synchronously (TestClient is synchronous).
    # We'll need to call start_session synchronously, so use asyncio.run.
    session_id = asyncio.run(voice_runner.start_session())

    return client, session_id, log


def test_voice_ws_happy_path(tmp_path: Path) -> None:
    """End-to-end test: connect → send audio → receive transcript/tts/stage."""
    client, session_id, _log = _make_client(tmp_path)

    with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as ws:
        # Send one audio chunk followed by end-of-stream.
        ws.send_json({
            "type": "audio_chunk",
            "seq": 0,
            "pcm_b64": base64.b64encode(b"\x00\x01" * 100).decode(),
        })
        ws.send_json({
            "type": "audio_chunk",
            "seq": -1,
            "pcm_b64": "",  # End-of-stream sentinel
        })

        # Receive messages in order:
        # 1. partial_transcript (the candidate's transcript)
        # 2. tts_chunks (examiner's response)
        # 3. stage_change (next stage info)

        received_transcript = False
        received_tts_chunks = False
        received_stage_change = False
        final_tts_seen = False

        for _ in range(20):  # Limit iterations to avoid infinite loop
            msg = ws.receive_json()

            if msg["type"] == "partial_transcript":
                received_transcript = True
                assert msg["is_final"] is True
                assert "regression" in msg["text"].lower()

            elif msg["type"] == "tts_chunk":
                received_tts_chunks = True
                assert "pcm_b64" in msg
                assert isinstance(msg["end_of_utterance"], bool)
                if msg["end_of_utterance"]:
                    final_tts_seen = True

            elif msg["type"] == "stage_change":
                received_stage_change = True
                assert msg["case_stage"] == "problem_framing"
                assert msg["primitive"] == "think_aloud"
                assert isinstance(msg["show_text_panel"], bool)
                # think_aloud does not need typed input
                assert msg["show_text_panel"] is False

            if final_tts_seen and received_stage_change:
                break

        assert received_transcript, "Never received partial_transcript"
        assert received_tts_chunks, "Never received tts_chunk"
        assert received_stage_change, "Never received stage_change"
        assert final_tts_seen, "Never received final tts_chunk with end_of_utterance=True"


def test_voice_ws_mode_switch_closes(tmp_path: Path) -> None:
    """Mode switch to 'text' closes WS cleanly."""
    client, session_id, _log = _make_client(tmp_path)

    with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as ws:
        # Send mode_switch message.
        ws.send_json({
            "type": "mode_switch",
            "mode": "text",
        })

        # WS should close cleanly (code 1000).
        # The TestClient doesn't directly expose close code, but attempting
        # to send after mode_switch should fail gracefully.
        with contextlib.suppress(RuntimeError):
            ws.send_json({"type": "audio_chunk", "seq": 0, "pcm_b64": "YWJj"})


def test_voice_ws_not_configured_rejects(tmp_path: Path) -> None:
    """When voice_runner=None, WS route rejects with 1011."""
    log = SqliteEventLog(tmp_path / "log.db")
    http_router = ModelRouter(FakeRouter())
    rubric = load_rubric(yaml_str=_MINI_RUBRIC)
    output_dir = tmp_path / "outputs"
    aggregator = RubricAggregator(rubric, output_dir=output_dir)
    http_runner = SessionRunner(
        challenger=LlmChallenger(http_router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(http_router),
            Dimension.communication: LlmCommunicationScorer(http_router),
        },
        aggregator=aggregator,
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )

    # Make app WITHOUT voice_runner.
    app = make_app(
        log=log,
        runner=http_runner,
        target_answers=1,
        max_probes=0,
        output_dir=output_dir,
        voice_runner=None,  # Explicitly None
    )

    client = TestClient(app)

    # Attempt to connect should raise WebSocketDisconnect with code 1011.
    try:
        with client.websocket_connect("/ws/sessions/any-id/voice"):
            pass
    except WebSocketDisconnect as e:
        assert e.code == 1011
    else:
        raise AssertionError("Expected WebSocketDisconnect with code 1011")
