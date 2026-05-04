from __future__ import annotations

from dataclasses import dataclass
from typing import Any

type JsonSchema = dict[str, Any]


@dataclass(frozen=True)
class JsonSchemaRequest:
    name: str
    schema: JsonSchema
    max_completion_tokens: int | None = None


def response_format(name: str, schema: JsonSchema) -> JsonSchema:
    """Return OpenAI's provider-native structured output response_format."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def scoring_result_schema(dimensions: tuple[str, ...]) -> JsonSchema:
    """Schema for scorer JSON responses with normalized signal values."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["signals"],
        "properties": {
            "signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "dimension",
                        "value",
                        "confidence",
                        "source_refs",
                        "justification",
                    ],
                    "properties": {
                        "dimension": {
                            "type": "string",
                            "enum": list(dimensions),
                        },
                        "value": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "source_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "justification": {"type": "string"},
                    },
                },
            },
        },
    }


def examiner_outcome_schema() -> JsonSchema:
    """Schema for examiner probe-or-close decisions."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "text", "reason", "rationale", "primitive_hint"],
        "properties": {
            "action": {"type": "string", "enum": ["probe", "close"]},
            "text": {"type": ["string", "null"]},
            "reason": {
                "type": ["string", "null"],
                "enum": [
                    "coverage_saturated",
                    "time_capped",
                    "examiner_pivot",
                    "max_probes",
                    None,
                ],
            },
            "rationale": {"type": "string"},
            "primitive_hint": {"type": ["string", "null"]},
        },
    }
