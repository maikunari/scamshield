"""ScamShield skills as plain JSON-in / JSON-out tools, ready to be served by a stdio MCP server unchanged.

``Toolbox(pipeline).specs()`` is the tool list (``name``, ``description``, ``inputSchema``, the shape MCP's
``tools/list`` wants) and ``Toolbox(pipeline).call(name, arguments)`` runs one and returns a JSON-serialisable dict.
There is no module-level state: everything a call needs hangs off the pipeline passed in, and every call builds its
own trace and search budget. Bad arguments raise ``ToolError`` (an MCP server maps it to ``isError``).
"""

from __future__ import annotations

from typing import Any

from .memory import Profile, compare_to_contacts
from .models import RawMessage, Trace
from .normalize import normalize
from .pipeline import Pipeline
from .skills import SKILLS, EvidenceDesk


class ToolError(ValueError):
    pass


_STR = {"type": "string"}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "check_message",
        "description": "Run the full ScamShield pipeline on a message and return the verdict "
        "(label, score, red flags with evidence links, safe next step, a script for calling the parent) and its trace.",
        "inputSchema": {
            "type": "object",
            "required": ["body"],
            "properties": {
                "body": _STR,
                "sender": _STR,
                "channel": {"type": "string", "enum": ["sms", "email", "voicemail", "paste"]},
                "subject": _STR,
                "parent_id": {**_STR, "description": "Whose profile to compare against (default: the first parent)."},
            },
        },
    },
    {
        "name": "check_phone",
        "description": "Search public scam reports for a phone number (Tavily, cached for 24h).",
        "inputSchema": {"type": "object", "required": ["phone"], "properties": {"phone": _STR}},
    },
    {
        "name": "check_url",
        "description": "Search public scam/phishing reports about a link's domain. The link itself is never opened.",
        "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": _STR}},
    },
    {
        "name": "check_phrase",
        "description": "Search recent news and official warnings for a distinctive scam phrase (Tavily news topic).",
        "inputSchema": {"type": "object", "required": ["phrase"], "properties": {"phrase": _STR}},
    },
    {
        "name": "compare_to_contacts",
        "description": "Is this sender one of the parent's trusted contacts or known services, or imitating one?",
        "inputSchema": {
            "type": "object",
            "required": ["sender"],
            "properties": {"sender": _STR, "body": _STR, "parent_id": _STR},
        },
    },
]

_LOOKUP_ARG = {"check_phone": "phone", "check_url": "url", "check_phrase": "phrase"}


class Toolbox:
    def __init__(self, pipeline: Pipeline, default_parent_id: str = "michiko"):
        self.pipeline = pipeline
        self.default_parent_id = default_parent_id

    def specs(self) -> list[dict[str, Any]]:
        return [dict(s) for s in TOOL_SPECS]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name == "check_message":
            body = self._text(args, "body")
            raw = RawMessage(
                body=body,
                sender=args.get("sender") or None,
                channel=args.get("channel") or "paste",
                subject=args.get("subject") or None,
            )
            return self.pipeline.analyze(raw, args.get("parent_id") or self.default_parent_id).model_dump()
        if name in SKILLS:
            value = self._text(args, _LOOKUP_ARG[name])
            trace = Trace()
            desk = EvidenceDesk(self.pipeline.search, self.pipeline.store, self.pipeline.settings, trace, self.pipeline.budget)
            return {"lookup": SKILLS[name](desk, value).model_dump(), "trace": trace.model_dump()}
        if name == "compare_to_contacts":
            sender = self._text(args, "sender")
            parent_id = args.get("parent_id") or self.default_parent_id
            profile = self.pipeline.store.get_profile(parent_id) or Profile(id=parent_id, name=parent_id)
            message = normalize(RawMessage(body=args.get("body") or "", sender=sender))
            return compare_to_contacts(profile, message).model_dump()
        raise ToolError(f"unknown tool {name!r}")

    @staticmethod
    def _text(args: dict[str, Any], key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f"argument {key!r} is required and must be a non-empty string")
        return value
