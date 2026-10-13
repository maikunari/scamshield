"""JSON Schemas for the three model replies, sent as ``response_format: json_schema`` and repeated in the prompt.

They follow the strict subset (every property required, ``additionalProperties: false``, nullable via a type list)
so the same dict works on any model that supports structured output.
"""

from __future__ import annotations

IMPERSONATION = ["none", "bank", "delivery", "family", "police", "government", "tech_support", "utility", "other"]
_NULLABLE_STR = {"type": ["string", "null"]}
_BOOL = {"type": "boolean"}

TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["signals", "risk", "scam_type", "key_phrase", "language", "summary"],
    "properties": {
        "signals": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "urgency", "money_request", "impersonation", "secrecy_request",
                "credential_request", "link_present", "phone_present",
            ],
            "properties": {
                "urgency": _BOOL,
                "money_request": _BOOL,
                "impersonation": {"type": "string", "enum": IMPERSONATION},
                "secrecy_request": _BOOL,
                "credential_request": _BOOL,
                "link_present": _BOOL,
                "phone_present": _BOOL,
            },
        },
        "risk": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "scam_type": _NULLABLE_STR,
        "key_phrase": _NULLABLE_STR,
        "language": {"type": "string"},
        "summary": {"type": "string"},
    },
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lookups"],
    "properties": {
        "lookups": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["skill", "value"],
                "properties": {
                    "skill": {"type": "string", "enum": ["check_phone", "check_url", "check_phrase"]},
                    "value": {"type": "string"},
                },
            },
        }
    },
}

VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "red_flags", "safe_action", "family_script"],
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "red_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "evidence_url"],
                "properties": {"text": {"type": "string"}, "evidence_url": _NULLABLE_STR},
            },
        },
        "safe_action": {"type": "string"},
        "family_script": {"type": "string"},
    },
}

SCHEMAS = {"triage": TRIAGE_SCHEMA, "plan": PLAN_SCHEMA, "verdict": VERDICT_SCHEMA}
