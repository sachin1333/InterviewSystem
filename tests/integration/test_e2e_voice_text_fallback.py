from __future__ import annotations

import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.challenger.llm_challenger import LlmChallenger
from adapters.eventlog.sqlite_log import SqliteEventLog
from adapters.http.app import make_app
from adapters.http.session_runner import SessionRunner
from adapters.http.voice_runner import VoiceRunner
from adapters.llm.fake_router import FakeRouter
from adapters.llm.router import ModelRouter
from adapters.scorer.aggregator import RubricAggregator
from adapters.scorer.authenticity_scorer import AuthenticityScorer
from adapters.scorer.llm_communication_scorer import LlmCommunicationScorer
from adapters.scorer.llm_rationale_scorer import LlmRationaleScorer
from adapters.stt.contracts import SttPartial
from adapters.stt.fake_stt import FakeStt
from adapters.tts.fake_tts import FakeTts
from core.domain import Actor, Dimension
from core.events import ArtifactAttached, TurnPosted
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


def test_voice_mode_switch_falls_back_to_typed_post(tmp_path: Path) -> None:
    log = SqliteEventLog(tmp_path / "fallback.db")
    router = ModelRouter(FakeRouter())
    rubric = load_rubric(yaml_str=_MINI_RUBRIC)
    aggregator = RubricAggregator(rubric, output_dir=tmp_path / "outputs")
    runner = SessionRunner(
        challenger=LlmChallenger(router),
        scorers={
            Dimension.model_rationale: LlmRationaleScorer(router),
            Dimension.communication: LlmCommunicationScorer(router),
        },
        aggregator=aggregator,
        scored_dimensions=(Dimension.model_rationale, Dimension.communication),
    )
    voice_runner = VoiceRunner(
        log=log,
        router=router,
        stt=FakeStt(script=[SttPartial("I would start with the target metric.", True, 450)]),
        tts=FakeTts(bytes_per_char=2),
        communication_scorer=LlmCommunicationScorer(router),
        authenticity_scorer=AuthenticityScorer(),
    )
    app = make_app(
        log=log,
        runner=runner,
        output_dir=tmp_path / "outputs",
        voice_runner=voice_runner,
        voice_mode="on",
    )
    client = TestClient(app)

    create = client.post(
        "/sessions",
        data={"candidate_handle": "voice-user", "pack_id": "ds-ml-v1"},
        follow_redirects=False,
    )
    session_id = create.headers["location"].rsplit("/", 1)[-1]

    page = client.get(f"/sessions/{session_id}")
    assert page.status_code == 200
    assert "data-voice-mode=\"on\"" in page.text

    with client.websocket_connect(f"/ws/sessions/{session_id}/voice") as ws:
        ws.send_json({"type": "mode_switch", "mode": "text"})

    post = client.post(
        f"/sessions/{session_id}/turn",
        data={"answer": "Typed fallback answer", "code": "", "turn_nonce": "nonce-1"},
        follow_redirects=False,
    )
    assert post.status_code == 303

    events = log.get_session(session_id)
    assert any(
        isinstance(env.payload, TurnPosted)
        and env.payload.actor == Actor.candidate
        for env in events
    )
    assert any(
        isinstance(env.payload, ArtifactAttached)
        and env.payload.content == "Typed fallback answer"
        for env in events
    )
