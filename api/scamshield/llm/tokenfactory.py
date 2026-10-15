from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx

from ..config import model_key
from .base import LLMError, LLMRequest, LLMResponse

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TAGS = r"think|thinking|thought|reasoning"
_BLOCK = re.compile(rf"<({_TAGS})>.*?</\1>", re.DOTALL | re.IGNORECASE)
_STRAY_CLOSE = re.compile(rf"</(?:{_TAGS})>", re.IGNORECASE)
_OPEN = re.compile(rf"<(?:{_TAGS})>", re.IGNORECASE)

REPAIR_SYSTEM = (
    "You repair malformed model output. Reply with exactly one valid JSON object that follows the JSON Schema below "
    "and contains only information from the text you are given. No prose, no code fences.\nJSON Schema:\n{schema}"
)


def strip_reasoning(content: str) -> str:
    """Drop reasoning/thinking blocks. Nemotron chat templates may emit only the closing tag (the opening one is
    part of the prompt), or never close the block when the reply is cut off, so all three shapes are handled."""
    text = _BLOCK.sub("", content)
    closes = list(_STRAY_CLOSE.finditer(text))
    if closes:
        text = text[closes[-1].end() :]
    opened = _OPEN.search(text)
    if opened:
        text = text[: opened.start()]
    return text.strip()


def parse_json_content(content: str) -> dict:
    """Pull a JSON object out of a model reply (tolerates reasoning blocks, code fences, leading prose)."""
    text = _FENCE.sub("", strip_reasoning(content)).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError(f"model reply is not JSON: {content[:200]!r}") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"model reply is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("model reply is JSON but not an object")
    return value


class LLMHTTPError(LLMError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class ProbeResult:
    """Outcome of a single, fallback-free ``json_schema`` request (used by the smoke script)."""

    model: str
    ok: bool
    detail: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0


class TokenFactoryClient:
    """Nebius Token Factory (OpenAI-compatible ``/chat/completions``). Only host contacted: the configured base URL.

    Structured output is asked for in three steps, each used only when the previous one fails:
    ``response_format: json_schema`` (schema also repeated in the system prompt), then ``json_object``, then one repair
    call that asks the model to fix its own malformed reply. A model that rejects ``json_schema`` with a 400/422 is
    remembered and goes straight to ``json_object`` afterwards.
    """

    name = "nebius-token-factory"
    offline = False

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
        backoff: float = 1.0,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
            transport=transport,
        )
        self._retries = retries
        self._backoff = backoff
        self._no_schema: set[str] = set()  # lowercase ids of models that rejected json_schema

    # --- public -----------------------------------------------------------------------------------
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        start = time.perf_counter()
        system = request.system
        if request.schema:
            system += "\n\nYour reply must validate against this JSON Schema:\n" + json.dumps(request.schema)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": request.user}]
        tokens_in = tokens_out = 0
        served_model = request.model
        last_content = ""
        last_error: Exception | None = None

        modes = ["json_object"]
        if request.schema and model_key(request.model) not in self._no_schema:
            modes.insert(0, "json_schema")
        for mode in modes:
            try:
                payload = self._post(self._body(request.model, messages, mode, request.schema, request.task, request.thinking))
            except LLMHTTPError as exc:
                last_error = exc
                if mode == "json_schema" and exc.status in (400, 422):
                    self._no_schema.add(model_key(request.model))
                    continue
                raise
            content, usage, served_model = self._unpack(payload, request.model)
            tokens_in += usage[0]
            tokens_out += usage[1]
            last_content = content
            try:
                data = self._checked(content, request.schema)
            except LLMError as exc:
                last_error = exc
                continue
            return self._response(data, served_model, tokens_in, tokens_out, start, mode)

        # Repair: ask the model to fix its last reply. Nothing to repair if it never produced text.
        if last_content.strip():
            repair = [
                {"role": "system", "content": REPAIR_SYSTEM.format(schema=json.dumps(request.schema or {}))},
                {"role": "user", "content": last_content[:6000]},
            ]
            payload = self._post(self._body(request.model, repair, "json_object", None, request.task, request.thinking))
            content, usage, served_model = self._unpack(payload, request.model)
            tokens_in += usage[0]
            tokens_out += usage[1]
            try:
                data = self._checked(content, request.schema)
            except LLMError as exc:
                last_error = exc
            else:
                return self._response(data, served_model, tokens_in, tokens_out, start, "repair")
        raise LLMError(f"Token Factory reply unusable ({request.task}, {request.model}): {last_error}") from last_error

    def probe_json_schema(self, model: str, schema: dict, system: str, user: str) -> ProbeResult:
        """One ``json_schema`` request with no fallback: does this model really honour structured output?"""
        start = time.perf_counter()
        messages = [
            {"role": "system", "content": system + "\n\nYour reply must validate against this JSON Schema:\n" + json.dumps(schema)},
            {"role": "user", "content": user},
        ]
        try:
            payload = self._post(self._body(model, messages, "json_schema", schema, "probe"))
            content, usage, _ = self._unpack(payload, model)
            self._checked(content, schema)
        except LLMError as exc:
            return ProbeResult(model, False, str(exc), latency_ms=round((time.perf_counter() - start) * 1000, 1))
        return ProbeResult(model, True, "json_schema honoured", usage[0], usage[1], round((time.perf_counter() - start) * 1000, 1))

    # --- internals --------------------------------------------------------------------------------
    @staticmethod
    def _body(model: str, messages: list[dict], mode: str, schema: dict | None, task: str, thinking: bool = True) -> dict:
        body: dict = {"model": model, "messages": messages, "temperature": 0}
        if not thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if mode == "json_schema" and schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": f"scamshield_{task}", "schema": schema, "strict": True},
            }
        else:
            body["response_format"] = {"type": "json_object"}
        return body

    @staticmethod
    def _unpack(payload: dict, requested: str) -> tuple[str, tuple[int, int], str]:
        try:
            content = payload["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, AttributeError) as exc:
            raise LLMError(f"unexpected Token Factory response shape: {exc!r}") from exc
        usage = payload.get("usage") or {}
        return (
            content,
            (int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
            payload.get("model") or requested,
        )

    @staticmethod
    def _checked(content: str, schema: dict | None) -> dict:
        data = parse_json_content(content)
        missing = [k for k in (schema or {}).get("required", []) if k not in data]
        if missing:
            raise LLMError(f"model reply is missing required keys {missing}")
        return data

    @staticmethod
    def _response(data: dict, model: str, tokens_in: int, tokens_out: int, start: float, mode: str) -> LLMResponse:
        return LLMResponse(
            data=data,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
            mode=mode,
        )

    def _post(self, body: dict) -> dict:
        """POST with retries on 429/5xx and transport errors. Other HTTP errors raise ``LLMHTTPError``."""
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.post("chat/completions", json=body)
            except httpx.TransportError as exc:
                last = exc
                if attempt < self._retries:
                    time.sleep(self._backoff * (2**attempt))
                    continue
                break
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._retries:
                time.sleep(self._backoff * (2**attempt))
                continue
            if resp.status_code >= 400:
                raise LLMHTTPError(
                    resp.status_code, f"Token Factory {resp.status_code} for {body.get('model')}: {resp.text[:300]}"
                )
            try:
                return resp.json()
            except ValueError as exc:
                last = exc
                break
        raise LLMError(f"Token Factory call failed ({body.get('model')}): {last}") from last
