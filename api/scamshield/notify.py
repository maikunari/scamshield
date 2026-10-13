"""Thin HTTP clients for the two alert channels: the Telegram Bot API and Resend. No SDKs, one call each.

Secrets never leave this module: the Telegram token is part of the request URL and the Resend key is a header,
so nothing here logs a URL or a header, httpx's own request logging is silenced, and errors are reduced to a
status code or an exception class name before they are stored or logged anywhere.
"""

from __future__ import annotations

import logging

import httpx

# httpx logs "HTTP Request: POST https://api.telegram.org/bot<TOKEN>/sendMessage" at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TELEGRAM_API = "https://api.telegram.org"
RESEND_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 5.0


class DeliveryError(RuntimeError):
    """A send failed. The message is safe to store and log (no URL, no credentials)."""


def _fail(what: str, exc: Exception) -> DeliveryError:
    if isinstance(exc, httpx.HTTPStatusError):
        return DeliveryError(f"{what}: HTTP {exc.response.status_code}")
    return DeliveryError(f"{what}: {type(exc).__name__}")


class TelegramClient:
    def __init__(self, token: str, http: httpx.Client):
        self._token = token
        self._http = http

    def call(self, method: str, **params: object) -> dict:
        try:
            r = self._http.post(f"{TELEGRAM_API}/bot{self._token}/{method}", json=params, timeout=TIMEOUT_SECONDS)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _fail(f"telegram {method}", exc) from None
        if not data.get("ok"):
            raise DeliveryError(f"telegram {method}: {data.get('error_code', 'error')}")
        return data

    def send_message(self, chat_id: str | int, text: str, buttons: list[list[dict]] | None = None) -> None:
        params: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        self.call("sendMessage", **params)

    def bot_username(self) -> str:
        return str(self.call("getMe")["result"]["username"])

    def set_webhook(self, url: str, secret: str) -> None:
        self.call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"])


class ResendClient:
    def __init__(self, api_key: str, sender: str, http: httpx.Client):
        self._key = api_key
        self._from = sender
        self._http = http

    def send(self, to: list[str], subject: str, html: str, text: str, idempotency_key: str) -> None:
        try:
            r = self._http.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._key}", "Idempotency-Key": idempotency_key},
                json={"from": self._from, "to": to, "subject": subject, "html": html, "text": text},
                timeout=TIMEOUT_SECONDS,
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise _fail("resend send", exc) from None
