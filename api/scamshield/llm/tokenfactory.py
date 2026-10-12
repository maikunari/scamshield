from __future__ import annotations

import json
import re
import time

import httpx

from .base import LLMError, LLMRequest, LLMResponse

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_json_content(content: str) -> dict:
    """Pull a JSON object out of a model reply (tolerates <think> blocks, code fences, leading prose)."""
    text = _FENCE.sub("", _THINK.sub("", content).strip()).strip()
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


class TokenFactoryClient:
    """Nebius Token Factory (OpenAI-compatible ``/chat/completions``). Only host contacted: the configured base URL."""

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

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        start = time.perf_counter()
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.post("chat/completions", json=body)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._retries:
                    time.sleep(self._backoff * (2**attempt))
                    continue
                resp.raise_for_status()
                payload = resp.json()
                content = payload["choices"][0]["message"]["content"]
                usage = payload.get("usage") or {}
                return LLMResponse(
                    data=parse_json_content(content),
                    model=payload.get("model") or request.model,
                    tokens_in=int(usage.get("prompt_tokens", 0)),
                    tokens_out=int(usage.get("completion_tokens", 0)),
                    latency_ms=round((time.perf_counter() - start) * 1000, 1),
                )
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last = exc
                if attempt < self._retries and isinstance(exc, httpx.TransportError):
                    time.sleep(self._backoff * (2**attempt))
                    continue
                break
        raise LLMError(f"Token Factory call failed ({request.task}, {request.model}): {last}") from last
