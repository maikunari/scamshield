"""Lookup planning: the candidate set, a deterministic default plan, and validation of a model-proposed plan."""

from __future__ import annotations

from .normalize import to_e164
from .redact import redact

SKILL_NAMES = {"check_phone", "check_url", "check_phrase"}


def default_plan(candidates: dict, key_phrase: str | None) -> list[dict]:
    picks: list[dict] = []
    if candidates["urls"]:
        picks.append({"skill": "check_url", "value": candidates["urls"][0]})
    if candidates["phones"]:
        picks.append({"skill": "check_phone", "value": candidates["phones"][0]})
    if key_phrase:
        picks.append({"skill": "check_phrase", "value": key_phrase})
    picks += [{"skill": "check_url", "value": u} for u in candidates["urls"][1:]]
    picks += [{"skill": "check_phone", "value": p} for p in candidates["phones"][1:]]
    return picks


def validate_plan(raw_plan: object, candidates: dict, body: str, limit: int) -> list[dict]:
    """Keep only well-formed lookups on values that really come from the message. Phrases are PII-redacted
    before they can reach a search engine. Duplicates are dropped and the list is capped at ``limit``."""
    items = raw_plan.get("lookups") if isinstance(raw_plan, dict) else None
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    body_words = set(body.lower().split())
    for item in items or []:
        if not isinstance(item, dict):
            continue
        skill, value = item.get("skill"), item.get("value")
        if skill not in SKILL_NAMES or not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if skill == "check_phone":
            value = to_e164(value) or ""
            if value not in candidates["phones"]:
                continue
        elif skill == "check_url":
            if value not in candidates["urls"]:
                continue
        else:
            words = value.lower().split()
            in_body = " ".join(value.lower().split()) in " ".join(body.lower().split())
            if not 1 <= len(value) <= 120 or not (
                in_body or (words and sum(w in body_words for w in words) >= 0.6 * len(words))
            ):
                continue
            value = redact(value).text
        if (skill, value) in seen:
            continue
        seen.add((skill, value))
        out.append({"skill": skill, "value": value})
    return out[:limit]


def candidates_for(message_urls: list[str], message_phones: list[str], sender: str | None, trusted: bool) -> dict:
    phones = list(message_phones)
    if sender and sender.startswith("+") and sender[1:].isdigit() and not trusted and sender not in phones:
        phones.insert(0, sender)
    return {"urls": list(message_urls), "phones": phones}


