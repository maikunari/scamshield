"""Per-parent memory in SQLite: profile (trusted contacts, known services), analysis results and
the 24h search cache. One connection guarded by a lock keeps ``:memory:`` and threaded use simple."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .models import AnalysisResult, ContactMatch, Message
from .normalize import domain_of, normalize_sender, to_e164

SCHEMA = """
CREATE TABLE IF NOT EXISTS parents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, profile_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, created_at TEXT NOT NULL,
    label TEXT NOT NULL, score INTEGER NOT NULL, body_hash TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_cache (
    key TEXT PRIMARY KEY, created_at TEXT NOT NULL, hits_json TEXT NOT NULL
);
"""


class Contact(BaseModel):
    name: str
    relationship: str = ""
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)


class KnownService(BaseModel):
    name: str
    kind: str = ""  # bank, pharmacy, delivery, clinic ...
    domains: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    senders: list[str] = Field(default_factory=list)  # email addresses or sender ids


class Profile(BaseModel):
    """Parent profile. ``store_green_bodies`` is the family opt-in to keep 🟢 message bodies."""

    id: str
    name: str
    languages: list[str] = Field(default_factory=lambda: ["en"])
    trusted_contacts: list[Contact] = Field(default_factory=list)
    known_services: list[KnownService] = Field(default_factory=list)
    store_green_bodies: bool = False


def _phones(values: list[str]) -> set[str]:
    return {p for v in values if (p := to_e164(v))}


def compare_to_contacts(profile: Profile, message: Message) -> ContactMatch:
    """Skill ``compare_to_contacts``: is the sender someone this parent knows?"""
    sender = message.sender
    if sender:
        for c in profile.trusted_contacts:
            if sender in _phones(c.phones) or sender in {normalize_sender(e) for e in c.emails}:
                return ContactMatch(
                    status="trusted",
                    name=c.name,
                    relationship=c.relationship,
                    reason=f"sender matches trusted contact {c.name}",
                )
        for s in profile.known_services:
            sender_domain = sender.split("@", 1)[1] if "@" in sender else ""
            by_sender = sender in {normalize_sender(x) for x in s.senders} or sender in _phones(s.phones)
            by_domain = sender_domain and sender_domain in {d.lower() for d in s.domains}
            if by_sender or by_domain:
                return ContactMatch(
                    status="known_service",
                    name=s.name,
                    relationship=s.kind,
                    reason=f"sender matches known service {s.name}",
                )
    return ContactMatch(status="unknown", reason="sender is not in the trusted contacts or known services")


def service_owns_all_links(profile: Profile, match: ContactMatch, message: Message) -> bool:
    """True when the sender is a known service and every link in the message is on that service's domains."""
    if match.status != "known_service":
        return False
    service = next((s for s in profile.known_services if s.name == match.name), None)
    if service is None:
        return False
    domains = {d.lower() for d in service.domains}
    return all(
        any(domain_of(u) == d or domain_of(u).endswith("." + d) for d in domains) for u in message.urls
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)

    # --- profiles -------------------------------------------------------------------------------
    def save_profile(self, profile: Profile) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO parents(id, name, profile_json) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, profile_json=excluded.profile_json",
                (profile.id, profile.name, profile.model_dump_json()),
            )

    def get_profile(self, parent_id: str) -> Profile | None:
        with self._lock:
            row = self._db.execute("SELECT profile_json FROM parents WHERE id=?", (parent_id,)).fetchone()
        return Profile.model_validate_json(row["profile_json"]) if row else None

    # --- analyses -------------------------------------------------------------------------------
    def save_analysis(self, result: AnalysisResult, *, keep_body: bool) -> None:
        """Persist a result. A 🟢 body is dropped (hash + label stay) unless the family opted in."""
        body_hash = hashlib.sha256(result.message.body.encode()).hexdigest()
        stored = result
        if result.verdict.label == "green" and not keep_body:
            stored = result.model_copy(deep=True)
            stored.message.body = ""
            stored.message.subject = None
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?,?,?)",
                (
                    result.id,
                    result.parent_id,
                    result.created_at,
                    result.verdict.label,
                    result.verdict.score,
                    body_hash,
                    stored.model_dump_json(),
                ),
            )

    def get_analysis(self, analysis_id: str) -> AnalysisResult | None:
        with self._lock:
            row = self._db.execute("SELECT result_json FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        return AnalysisResult.model_validate_json(row["result_json"]) if row else None

    # --- search cache ---------------------------------------------------------------------------
    def cache_get(self, key: str, ttl_seconds: int) -> list[dict] | None:
        with self._lock:
            row = self._db.execute("SELECT created_at, hits_json FROM search_cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["created_at"]) < _now() - timedelta(seconds=ttl_seconds):
            return None
        return json.loads(row["hits_json"])

    def cache_put(self, key: str, hits: list[dict]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO search_cache VALUES(?,?,?)",
                (key, _now().isoformat(), json.dumps(hits)),
            )
