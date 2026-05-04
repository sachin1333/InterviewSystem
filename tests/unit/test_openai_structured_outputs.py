from __future__ import annotations

import json
from typing import Any

from adapters.llm.router import ModelRouter
from adapters.llm.structured_schema import (
    JsonSchemaRequest,
    examiner_outcome_schema,
    response_format,
    scoring_result_schema,
)


class _RecordingProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        *,
        tier: str,
        prompt: str,
        stream: bool = False,
        timeout: float | None = None,
        json_schema: dict[str, object] | None = None,
        schema_name: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "tier": tier,
                "prompt": prompt,
                "stream": stream,
                "timeout": timeout,
                "json_schema": json_schema,
                "schema_name": schema_name,
                "max_completion_tokens": max_completion_tokens,
            }
        )
        return self.response


def test_response_format_uses_openai_strict_json_schema_shape() -> None:
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    assert response_format("examiner_outcome", schema) == {
        "type": "json_schema",
        "json_schema": {
            "name": "examiner_outcome",
            "strict": True,
            "schema": schema,
        },
    }


def test_json_schema_request_dataclass_carries_schema_name_and_token_limit() -> None:
    schema = {"type": "object"}

    request = JsonSchemaRequest(
        name="scoring_result",
        schema=schema,
        max_completion_tokens=500,
    )

    assert request.name == "scoring_result"
    assert request.schema is schema
    assert request.max_completion_tokens == 500


def test_scoring_result_schema_restricts_dimensions_and_numeric_scores() -> None:
    schema = scoring_result_schema(("problem_framing", "communication"))
    signals = schema["properties"]["signals"]
    signal = signals["items"]
    properties = signal["properties"]

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["signals"]
    assert signal["additionalProperties"] is False
    assert signal["required"] == [
        "dimension",
        "value",
        "confidence",
        "source_refs",
        "justification",
    ]
    assert properties["dimension"] == {
        "type": "string",
        "enum": ["problem_framing", "communication"],
    }
    assert properties["value"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert properties["confidence"] == {"type": "number", "minimum": 0, "maximum": 1}


def test_examiner_outcome_schema_restricts_action_and_close_reason() -> None:
    schema = examiner_outcome_schema()
    properties = schema["properties"]

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["action", "text", "reason", "rationale", "primitive_hint"]
    assert properties["action"] == {"type": "string", "enum": ["probe", "close"]}
    assert properties["reason"]["type"] == ["string", "null"]
    assert properties["reason"]["enum"] == [
        "coverage_saturated",
        "time_capped",
        "examiner_pivot",
        "max_probes",
        None,
    ]


def test_structured_call_json_passes_schema_to_provider_and_parses_direct_json() -> None:
    provider = _RecordingProvider('{"signals":[{"dimension":"communication","value":0.7,"confidence":0.8}]}')
    router = ModelRouter(provider, max_retries=1, sleep=lambda _: None)
    schema = scoring_result_schema(("communication",))

    response = router.call_json(
        tier="mid",
        prompt="score this",
        schema={"signals"},
        json_schema=schema,
        schema_name="scoring_result",
        max_completion_tokens=500,
    )

    assert response == {
        "signals": [{"dimension": "communication", "value": 0.7, "confidence": 0.8}]
    }
    assert provider.calls == [
        {
            "tier": "mid",
            "prompt": "score this",
            "stream": False,
            "timeout": 6.0,
            "json_schema": schema,
            "schema_name": "scoring_result",
            "max_completion_tokens": 500,
        }
    ]


def test_structured_call_json_does_not_extract_json_from_wrapped_text() -> None:
    provider = _RecordingProvider('Here is JSON: {"signals": []}')
    router = ModelRouter(provider, max_retries=1, sleep=lambda _: None)

    try:
        router.call_json(
            tier="mid",
            prompt="score this",
            schema={"signals"},
            json_schema=scoring_result_schema(("communication",)),
            schema_name="scoring_result",
        )
    except json.JSONDecodeError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("structured call_json must parse the provider response directly")
