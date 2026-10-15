"""Deterministic offline stand-in for the three model tiers.

It answers from the structured ``request.context`` with keyword heuristics (English + Japanese). It exists so the
pipeline, tests and eval run end-to-end without a key; it is NOT a measure of model quality. Token counts are
estimated from text length so the trace and cost columns are populated.
"""

from __future__ import annotations

import json
import re
import time

from ..planning import default_plan
from .base import LLMRequest, LLMResponse

I = re.IGNORECASE

URGENCY = re.compile(
    r"urgent|immediately|right now|asap|today|tonight|within \d+ ?(?:hour|minute|day)s?|in \d+ hours?|final notice|"
    r"last chance|expires?|suspended|locked|至急|本日中|今日中|直ちに|今すぐ|急ぎ|停止",
    I,
)
MONEY = re.compile(
    r"gift ?card|wire|zelle|western union|bail|bitcoin|crypto|prepaid|payment|\bpay\b|\bfee\b|\bfine\b|deposit|"
    r"transfer|send \$|\$\s?\d|¥\s?\d|\d\s?円|donat|振り込|振込|送金|支払|現金|ギフト|ATM",
    I,
)
PAYMENT_METHOD = re.compile(r"gift ?card|wire|zelle|western union|bitcoin|crypto|prepaid|ギフト", I)
SECRECY = re.compile(r"don'?t tell|do not tell|keep (?:this|it) (?:a )?secret|between us|誰にも言わ|内緒|秘密", I)
CREDENTIAL = re.compile(
    r"verify|log ?in|sign ?in|confirm your|update your|claim|click|enter your|call (?:us|now|back|this number)|"
    r"call microsoft|ログイン|クリック|ご確認|手続き|入力",
    I,
)

# (scam_type, impersonation, pattern). First match wins, so order matters.
TYPES: list[tuple[str, str, re.Pattern[str]]] = [
    ("family_emergency", "family", re.compile(r"grandma|grandpa|grandson|granddaughter|it'?s me|new (?:phone|number)|お母さん|母さん|おばあちゃん|俺だよ|オレオレ", I)),
    ("police_impersonation", "police", re.compile(r"officer|sheriff|police|warrant|arrest|jury duty|警察|逮捕|警視庁", I)),
    ("tech_support", "tech_support", re.compile(r"virus|infected|microsoft|tech support|your computer|ウイルス", I)),
    ("lottery_prize", "other", re.compile(r"you(?:'ve| have)? won|congratulations|lottery|sweepstakes|当選", I)),
    ("investment_crypto", "other", re.compile(r"crypto|bitcoin|guaranteed (?:return|profit)|trading platform|\d{2,}% returns|暗号資産|投資", I)),
    ("romance", "other", re.compile(r"my love|darling|sweetheart|i love you|lonely|oil rig|overseas contract", I)),
    ("utility_shutoff", "utility", re.compile(r"(?:electric|power|gas|water|utility)[^.]{0,60}(?:disconnect|shut ?off|cut off)|disconnected|電気|ガス|水道", I)),
    ("bank_phishing", "bank", re.compile(r"\bbank\b|account (?:has been )?(?:locked|suspended|frozen|statement)|銀行|口座|キャッシュカード", I)),
    ("government_refund", "government", re.compile(r"\birs\b|tax refund|revenue|social security|medicare|還付|税務署|市役所", I)),
    ("parcel_delivery", "delivery", re.compile(r"parcel|package|redeliver|courier|tracking|out for delivery|宅配|お荷物|再配達|不在", I)),
    ("charity_solicitation", "other", re.compile(r"donat|charity|relief fund|寄付", I)),
    ("marketing", "other", re.compile(r"\bsale\b|% off|discount|limited time|セール", I)),
]
SCAMMY_TYPES = {"police_impersonation", "tech_support", "lottery_prize", "investment_crypto", "romance"}
INSTITUTIONAL = {"bank", "delivery", "government", "utility", "police", "tech_support"}

FAMILY_POINTS = {
    "family_emergency": [
        "A text pretending to be a grandchild in trouble, asking for money.",
        "The number isn't one she knows, and it asks her to keep it secret.",
        "Tell her not to send money or gift cards.",
        "Call the grandchild on his usual number to show he's fine.",
    ],
    "parcel_delivery": [
        "A text claiming a parcel couldn't be delivered, with a link to pay a fee.",
        "Couriers don't charge redelivery fees through text links.",
        "Tell her not to tap the link or enter card details.",
    ],
    "bank_phishing": [
        "A message claiming her bank account is locked, with a link to log in.",
        "The bank never asks her to log in through a text or email link.",
        "Tell her not to tap it; call the number on the back of her card.",
    ],
    "government_refund": [
        "A message offering a government refund through a link.",
        "Tax and benefit offices don't send refund links by text or email.",
        "Tell her not to click it or share any details.",
    ],
    "tech_support": [
        "A warning that her computer is infected, with a number to call.",
        "Real virus warnings never ask you to phone anyone.",
        "Tell her to close the page and not call the number.",
    ],
    "police_impersonation": [
        "A call claiming to be the police about a warrant, demanding payment.",
        "Police never take payment over the phone or in gift cards.",
        "Tell her not to call back or pay anything.",
        "If she's worried, call the local police on their published number.",
    ],
}
DEFAULT_POINTS = [
    "A message from a sender she doesn't know.",
    "It has the marks of a scam: pressure, a link or a request for money.",
    "Tell her not to reply, click, pay or call back.",
]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


URL_IN_TEXT = re.compile(r"https?://\S+")


def _key_phrase(body: str, pattern: re.Pattern[str]) -> str | None:
    """The sentence around the first scam-pattern hit, trimmed to something worth searching for."""
    m = pattern.search(body)
    if not m:
        return None
    for sm in re.finditer(r"[^.!?。！？\n]+[.!?。！？]?", body):
        if sm.start() <= m.start() < sm.end():
            sentence = URL_IN_TEXT.sub("", sm.group(0)).strip(" .,:;!?「」【】\n")
            words = sentence.split()
            if len(words) > 1:
                return " ".join(words[:8])
            return sentence[:20] or None
    return None


def heuristic_triage(message: dict) -> dict:
    text = f"{message.get('subject') or ''} {message['body']}"
    urls, phones = message.get("urls", []), message.get("phones", [])
    scam_type, impersonation, window = None, "none", None
    for t, imp, pattern in TYPES:
        m = pattern.search(text)
        if m:
            scam_type, impersonation, window = t, imp, _key_phrase(message['body'], pattern)
            break
    signals = {
        "urgency": bool(URGENCY.search(text)),
        "money_request": bool(MONEY.search(text)),
        "impersonation": impersonation if impersonation != "other" else "none",
        "secrecy_request": bool(SECRECY.search(text)),
        "credential_request": bool(CREDENTIAL.search(text)),
        "link_present": bool(urls),
        "phone_present": bool(phones),
    }
    points = (
        signals["urgency"]
        + 2 * signals["money_request"]
        + (signals["impersonation"] != "none")
        + 2 * signals["secrecy_request"]
        + 2 * signals["credential_request"]
        + signals["link_present"]
        + signals["phone_present"]
        + 2 * (scam_type in SCAMMY_TYPES)
    )
    risk = "none" if points == 0 else "low" if points == 1 else "medium" if points <= 3 else "high"
    language = "ja" if re.search(r"[぀-ヿ一-鿿]", text) else "en"
    return {
        "signals": signals,
        "risk": risk,
        "scam_type": scam_type if risk != "none" else None,
        "key_phrase": window if risk != "none" and scam_type not in (None, "marketing") else None,
        "language": language,
        "summary": f"{scam_type or 'ordinary message'}; signals={[k for k, v in signals.items() if v and v != 'none']}",
        "_points": points,
    }


def heuristic_plan(ctx: dict) -> dict:
    return {"lookups": default_plan(ctx["candidates"], ctx["triage"].get("key_phrase"))[:3]}


def _first_hit_url(lookups: list[dict], skill: str) -> str | None:
    for lk in lookups:
        if lk["skill"] == skill and lk["hits"]:
            return lk["hits"][0]["url"]
    return None


def heuristic_verdict(ctx: dict) -> dict:
    triage, contact, lookups = ctx["triage"], ctx["contact"], ctx["lookups"]
    sig = triage["signals"]
    unknown = contact["status"] == "unknown"
    points = (
        sig["urgency"]
        + 2 * sig["money_request"]
        + (sig["impersonation"] != "none")
        + 2 * sig["secrecy_request"]
        + 2 * sig["credential_request"]
        + sig["link_present"]
        + sig["phone_present"]
        + 2 * (triage.get("scam_type") in SCAMMY_TYPES)
        + 2 * (unknown and sig["impersonation"] in INSTITUTIONAL)
        + 3 * bool(PAYMENT_METHOD.search(ctx["message"]["body"]))
    )
    reported = any(lk["hits"] for lk in lookups)
    score = min(100, 8 + 13 * points + (15 if reported else 0))
    flags: list[dict] = []

    def flag(text: str, url: str | None = None) -> None:
        flags.append({"text": text, "evidence_url": url})

    if sig["impersonation"] == "family" and unknown:
        flag("Claims to be a family member but the number is not one of the parent's trusted contacts.")
    elif sig["impersonation"] in INSTITUTIONAL and unknown:
        flag(f"Poses as an official sender ({sig['impersonation'].replace('_', ' ')}) but is not a known contact or service.")
    if sig["money_request"]:
        flag("Asks for money, a fee or a payment.", _first_hit_url(lookups, "check_phrase"))
    if sig["urgency"]:
        flag("Pressures you to act immediately.")
    if sig["secrecy_request"]:
        flag("Asks you to keep it secret from family.")
    if sig["credential_request"] or sig["link_present"]:
        flag("Asks you to follow a link, log in or call a number from the message.", _first_hit_url(lookups, "check_url"))
    if sig["phone_present"]:
        url = _first_hit_url(lookups, "check_phone")
        if url:
            flag("The phone number has been reported by others.", url)
    for lk in lookups:
        if lk["skill"] == "check_url" and lk["hits"] and not any(f["evidence_url"] == lk["hits"][0]["url"] for f in flags):
            flag("The link's domain has been reported as a scam or phishing site.", lk["hits"][0]["url"])
    stype = triage.get("scam_type")
    if score >= 70:
        safe = "Do not reply, click, pay or call back. Show this to your family."
    elif score >= 30:
        safe = "Be careful: don't click links or share details. Ask your family before doing anything."
    else:
        safe = "This looks fine. No action needed."
    return {
        "score": score,
        "red_flags": flags[:6],
        "safe_action": safe,
        "family_points": (FAMILY_POINTS.get(stype or "", DEFAULT_POINTS) if score >= 30 else []),
    }


class FakeLLMClient:
    name = "fake-llm"
    offline = True

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        start = time.perf_counter()
        ctx = request.context
        if request.task == "triage":
            data = heuristic_triage(ctx["message"])
            data.pop("_points", None)
        elif request.task == "plan":
            data = heuristic_plan(ctx)
        elif request.task == "verdict":
            data = heuristic_verdict(ctx)
        else:
            raise ValueError(f"unknown task {request.task!r}")
        out = json.dumps(data, ensure_ascii=False)
        return LLMResponse(
            data=data,
            model=request.model,
            tokens_in=_approx_tokens(request.system + request.user),
            tokens_out=_approx_tokens(out),
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            mode="fake",
        )
