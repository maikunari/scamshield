"""Per-parent memory in SQLite: profile (trusted contacts, known services), analysis results and
the 24h search cache. One connection guarded by a lock keeps ``:memory:`` and threaded use simple."""

from __future__ import annotations

import hashlib
import json
import re
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
CREATE TABLE IF NOT EXISTS verdict_cache (
    key TEXT PRIMARY KEY, created_at TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, at TEXT NOT NULL,
    kind TEXT NOT NULL, tier TEXT NOT NULL, usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS spend_day ON spend(day);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, analysis_id TEXT NOT NULL, body_hash TEXT NOT NULL,
    created_at TEXT NOT NULL, token TEXT NOT NULL,
    acked_at TEXT, acked_by TEXT, acked_via TEXT,
    UNIQUE(parent_id, body_hash)
);
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id TEXT NOT NULL, contact TEXT NOT NULL, channel TEXT NOT NULL,
    status TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}', at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alert_deliveries_contact ON alert_deliveries(contact, at);
CREATE TABLE IF NOT EXISTS telegram_links (
    code TEXT PRIMARY KEY, parent_id TEXT NOT NULL, contact TEXT NOT NULL, created_at TEXT NOT NULL, chat_id TEXT
);
CREATE TABLE IF NOT EXISTS family_auth (
    parent_id TEXT PRIMARY KEY, salt TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analyses_parent ON analyses(parent_id, created_at);
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


MIN_BRAND_CHARS = 5


def _squash(host: str) -> str:
    return re.sub(r"[^a-z0-9]", "", host.lower())


def lookalike_service(profile: Profile, message: Message) -> tuple[KnownService, str] | None:
    """A known service whose domain this message imitates: the sender's or a link's host is not the service's
    domain (or a subdomain of it) but contains its brand label with the dots and hyphens removed, so
    ``lakeviewbank-online.test`` and ``lakeviewbank.example.com.evil.test`` both imitate ``lakeviewbank.example.com``."""
    hosts = [domain_of(u) for u in message.urls]
    if message.sender and "@" in message.sender:
        hosts.append(message.sender.split("@", 1)[1])
    for host in dict.fromkeys(h.lower() for h in hosts if h):
        for service in profile.known_services:
            for owned in (d.lower() for d in service.domains):
                if host == owned or host.endswith("." + owned):
                    continue
                brand = _squash(owned.split(".")[0])
                if len(brand) >= MIN_BRAND_CHARS and brand in _squash(host):
                    return service, host
    return None


def compare_to_contacts(profile: Profile, message: Message) -> ContactMatch:
    """Skill ``compare_to_contacts``: is the sender someone this parent knows? Also flags a sender or link that
    imitates one of the parent's known services (``lookalike_of``)."""
    match = _match_sender(profile, message)
    if match.status != "trusted" and (look := lookalike_service(profile, message)):
        service, host = look
        match.lookalike_of = service.name
        note = f"{host} imitates {service.name}'s address ({', '.join(service.domains)}) but is not it"
        match.reason = f"{match.reason}; {note}" if match.status != "unknown" else note
    return match


def _match_sender(profile: Profile, message: Message) -> ContactMatch:
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

    def recent_analyses(self, parent_id: str, limit: int = 30) -> list[tuple[str, AnalysisResult]]:
        """Newest first, as ``(body_hash, result)``. The hash joins a check to its alert."""
        with self._lock:
            rows = self._db.execute(
                "SELECT body_hash, result_json FROM analyses WHERE parent_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (parent_id, limit),
            ).fetchall()
        return [(r["body_hash"], AnalysisResult.model_validate_json(r["result_json"])) for r in rows]

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

    # --- verdict cache --------------------------------------------------------------------------
    def verdict_cache_get(self, key: str, ttl_seconds: int) -> AnalysisResult | None:
        """A previous analysis for the same message/profile/models. Pinned entries (the warmed seed set) never expire."""
        with self._lock:
            row = self._db.execute(
                "SELECT created_at, pinned, result_json FROM verdict_cache WHERE key=?", (key,)
            ).fetchone()
        if not row:
            return None
        if not row["pinned"] and datetime.fromisoformat(row["created_at"]) < _now() - timedelta(seconds=ttl_seconds):
            return None
        return AnalysisResult.model_validate_json(row["result_json"])

    def verdict_cache_put(self, key: str, result: AnalysisResult, *, pinned: bool = False) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO verdict_cache VALUES(?,?,?,?)",
                (key, _now().isoformat(), int(pinned), result.model_dump_json()),
            )

    # --- spend ledger ---------------------------------------------------------------------------
    def spend_add(self, kind: str, tier: str, usd: float) -> None:
        """Append to the ledger. ``kind`` is ``model`` or ``tool``; ``tier`` is the tier that was actually called."""
        now = _now()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO spend(day, at, kind, tier, usd) VALUES(?,?,?,?,?)",
                (now.strftime("%Y-%m-%d"), now.isoformat(), kind, tier, usd),
            )

    def spend_total(self) -> float:
        with self._lock:
            return float(self._db.execute("SELECT COALESCE(SUM(usd), 0) FROM spend").fetchone()[0])

    def spend_today(self, tier: str) -> tuple[int, float]:
        """(calls, usd) booked against ``tier`` since 00:00 UTC."""
        day = _now().strftime("%Y-%m-%d")
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(usd), 0) FROM spend WHERE day=? AND tier=?", (day, tier)
            ).fetchone()
        return int(row[0]), float(row[1])

    # --- privacy: export, delete, retention -----------------------------------------------------
    # The spend ledger is never touched here: it holds no PII and the budget caps depend on it.
    def analyses_for_parent(self, parent_id: str) -> list[AnalysisResult]:
        with self._lock:
            rows = self._db.execute(
                "SELECT result_json FROM analyses WHERE parent_id=? ORDER BY created_at, rowid", (parent_id,)
            ).fetchall()
        return [AnalysisResult.model_validate_json(r["result_json"]) for r in rows]

    def telegram_links_for_parent(self, parent_id: str) -> list[dict]:
        """Which family contacts have a linked Telegram chat. Chat ids and unused codes stay out of the export."""
        with self._lock:
            rows = self._db.execute(
                "SELECT contact, created_at, chat_id IS NOT NULL AS linked FROM telegram_links "
                "WHERE parent_id=? ORDER BY created_at",
                (parent_id,),
            ).fetchall()
        return [{"contact": r["contact"], "created_at": r["created_at"], "linked": bool(r["linked"])} for r in rows]

    def _delete_alerts(self, where: str, args: tuple) -> int:
        ids = [r[0] for r in self._db.execute(f"SELECT id FROM alerts WHERE {where}", args)]
        for chunk in range(0, len(ids), 500):
            part = ids[chunk : chunk + 500]
            marks = ",".join("?" * len(part))
            self._db.execute(f"DELETE FROM alert_deliveries WHERE alert_id IN ({marks})", part)
            self._db.execute(f"DELETE FROM alerts WHERE id IN ({marks})", part)
        return len(ids)

    def delete_parent_data(self, parent_id: str) -> dict[str, int]:
        """Delete everything stored for one parent: profile (memory), checks with their traces, verdict-cache copies,
        alerts and deliveries, Telegram links. The search cache (queries hold phone numbers and domains from
        messages) is emptied too. Returns what was removed."""
        with self._lock, self._db:
            counts = {
                "checks": self._db.execute("DELETE FROM analyses WHERE parent_id=?", (parent_id,)).rowcount,
                "verdict_cache": self._db.execute(
                    "DELETE FROM verdict_cache WHERE json_extract(result_json, '$.parent_id')=?", (parent_id,)
                ).rowcount,
            }
            counts["alerts"] = self._delete_alerts("parent_id=?", (parent_id,))
            counts["telegram_links"] = self._db.execute(
                "DELETE FROM telegram_links WHERE parent_id=?", (parent_id,)
            ).rowcount
            counts["profile"] = self._db.execute("DELETE FROM parents WHERE id=?", (parent_id,)).rowcount
            counts["search_cache"] = self._db.execute("DELETE FROM search_cache").rowcount
        return counts

    def delete_analysis(self, parent_id: str, analysis_id: str) -> bool:
        """Delete one check and what was made from it: its alert and deliveries, and any verdict-cache entry for the
        same message (otherwise a repeat would replay the deleted text). False when the parent has no such check."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT body_hash FROM analyses WHERE id=? AND parent_id=?", (analysis_id, parent_id)
            ).fetchone()
            if not row:
                return False
            self._db.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
            self._delete_alerts("analysis_id=?", (analysis_id,))
            cached = self._db.execute(
                "SELECT key, result_json FROM verdict_cache WHERE json_extract(result_json, '$.parent_id')=?",
                (parent_id,),
            ).fetchall()
            for c in cached:
                result = AnalysisResult.model_validate_json(c["result_json"])
                if result.id == analysis_id or hashlib.sha256(result.message.body.encode()).hexdigest() == row["body_hash"]:
                    self._db.execute("DELETE FROM verdict_cache WHERE key=?", (c["key"],))
        return True

    def purge_older_than(self, cutoff: datetime) -> dict[str, int]:
        """Retention: delete checks, alerts and unpinned cached verdicts created before ``cutoff``. Profiles,
        Telegram links and the spend ledger stay."""
        stamp = cutoff.isoformat()
        with self._lock, self._db:
            counts = {
                "checks": self._db.execute("DELETE FROM analyses WHERE created_at<?", (stamp,)).rowcount,
                "verdict_cache": self._db.execute(
                    "DELETE FROM verdict_cache WHERE pinned=0 AND created_at<?", (stamp,)
                ).rowcount,
                "search_cache": self._db.execute("DELETE FROM search_cache WHERE created_at<?", (stamp,)).rowcount,
            }
            counts["alerts"] = self._delete_alerts("created_at<?", (stamp,))
        return counts

    # --- family alerts --------------------------------------------------------------------------
    def alert_create(
        self, alert_id: str, parent_id: str, analysis_id: str, body_hash: str, token: str, *, at: str | None = None
    ) -> tuple[str, bool]:
        """Insert the alert row. Returns (id, created): a parent gets one alert per message, so a repeat returns
        the earlier alert's id with created=False. ``at`` backdates the row (the demo family's history)."""
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO alerts(id, parent_id, analysis_id, body_hash, created_at, token) VALUES(?,?,?,?,?,?)",
                (alert_id, parent_id, analysis_id, body_hash, at or _now().isoformat(), token),
            )
            if cur.rowcount == 1:
                return alert_id, True
            row = self._db.execute(
                "SELECT id FROM alerts WHERE parent_id=? AND body_hash=?", (parent_id, body_hash)
            ).fetchone()
            return row["id"], False

    def alert_get(self, alert_id: str) -> dict | None:
        """The alert with its deliveries. Includes ``token``: strip it before it leaves the API."""
        with self._lock:
            row = self._db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            if not row:
                return None
            deliveries = self._db.execute(
                "SELECT contact, channel, status, target, detail, payload_json, at FROM alert_deliveries "
                "WHERE alert_id=? ORDER BY id",
                (alert_id,),
            ).fetchall()
        out = {k: row[k] for k in row.keys()}
        out["deliveries"] = [
            {**{k: d[k] for k in ("contact", "channel", "status", "target", "detail", "at")}, "payload": json.loads(d["payload_json"])}
            for d in deliveries
        ]
        return out

    def alerts_for_parent(self, parent_id: str) -> list[dict]:
        with self._lock:
            ids = [r[0] for r in self._db.execute(
                "SELECT id FROM alerts WHERE parent_id=? ORDER BY created_at DESC, rowid DESC", (parent_id,)
            )]
        return [a for i in ids if (a := self.alert_get(i))]

    def alert_delivery_add(
        self, alert_id: str, contact: str, channel: str, status: str, *, target: str = "", detail: str = "",
        payload: dict | None = None, at: str | None = None,
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO alert_deliveries(alert_id, contact, channel, status, target, detail, payload_json, at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (alert_id, contact, channel, status, target, detail, json.dumps(payload or {}), at or _now().isoformat()),
            )

    def alerts_to_contact_since(self, contact: str, since: datetime) -> int:
        """Alerts this family contact was sent (or would have been, when unconfigured) since ``since``.
        One alert counts once however many channels carried it."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(DISTINCT alert_id) FROM alert_deliveries WHERE contact=? AND at>=? "
                "AND status IN ('sent','recorded')",
                (contact, since.isoformat()),
            ).fetchone()
        return int(row[0])

    def alert_ack(self, alert_id: str, by: str, via: str, *, at: str | None = None) -> bool:
        """Mark acknowledged. True when this call did it, False when it was already acknowledged."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE alerts SET acked_at=?, acked_by=?, acked_via=? WHERE id=? AND acked_at IS NULL",
                (at or _now().isoformat(), by, via, alert_id),
            )
            return cur.rowcount == 1

    def alert_ids_by_body(self, parent_id: str) -> dict[str, str]:
        """``body_hash -> alert id`` for a parent: a repeated message shares the alert of its first appearance."""
        with self._lock:
            rows = self._db.execute("SELECT body_hash, id FROM alerts WHERE parent_id=?", (parent_id,)).fetchall()
        return {r["body_hash"]: r["id"] for r in rows}

    # --- family gate and demo ---------------------------------------------------------------------
    def auth_get(self, parent_id: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._db.execute("SELECT salt, hash FROM family_auth WHERE parent_id=?", (parent_id,)).fetchone()
        return (row["salt"], row["hash"]) if row else None

    def auth_set(self, parent_id: str, salt: str, digest: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO family_auth(parent_id, salt, hash) VALUES(?,?,?) "
                "ON CONFLICT(parent_id) DO UPDATE SET salt=excluded.salt, hash=excluded.hash",
                (parent_id, salt, digest),
            )

    def kv_get_or_set(self, key: str, make_value) -> str:
        """The stored value for ``key``, creating it with ``make_value()`` on first use."""
        with self._lock, self._db:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            if row:
                return row["value"]
            value = make_value()
            self._db.execute("INSERT INTO kv(key, value) VALUES(?,?)", (key, value))
            return value

    def clear_family_activity(self, parent_id: str) -> None:
        """Drop a parent's checks, alerts, deliveries and Telegram links (not the profile, caches or spend ledger)."""
        with self._lock, self._db:
            ids = [r[0] for r in self._db.execute("SELECT id FROM alerts WHERE parent_id=?", (parent_id,))]
            for alert_id in ids:
                self._db.execute("DELETE FROM alert_deliveries WHERE alert_id=?", (alert_id,))
            self._db.execute("DELETE FROM alerts WHERE parent_id=?", (parent_id,))
            self._db.execute("DELETE FROM analyses WHERE parent_id=?", (parent_id,))
            self._db.execute("DELETE FROM telegram_links WHERE parent_id=?", (parent_id,))

    # --- telegram linking -----------------------------------------------------------------------
    def telegram_link_create(self, code: str, parent_id: str, contact: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO telegram_links(code, parent_id, contact, created_at) VALUES(?,?,?,?)",
                (code, parent_id, contact, _now().isoformat()),
            )

    def telegram_link_claim(self, code: str, chat_id: str, max_age: timedelta) -> tuple[str, str] | None:
        """Bind an unused, unexpired code to a chat. Returns (parent_id, contact), or None for an unknown, used
        or expired code. A contact keeps only its newest chat."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT parent_id, contact, created_at FROM telegram_links WHERE code=? AND chat_id IS NULL", (code,)
            ).fetchone()
            if not row or datetime.fromisoformat(row["created_at"]) < _now() - max_age:
                return None
            self._db.execute(
                "DELETE FROM telegram_links WHERE parent_id=? AND contact=? AND chat_id IS NOT NULL",
                (row["parent_id"], row["contact"]),
            )
            self._db.execute("UPDATE telegram_links SET chat_id=? WHERE code=?", (chat_id, code))
            return row["parent_id"], row["contact"]

    def telegram_chat(self, parent_id: str, contact: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT chat_id FROM telegram_links WHERE parent_id=? AND contact=? AND chat_id IS NOT NULL",
                (parent_id, contact),
            ).fetchone()
        return row["chat_id"] if row else None

    def telegram_contact_for_chat(self, chat_id: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT parent_id, contact FROM telegram_links WHERE chat_id=?", (chat_id,)
            ).fetchone()
        return (row["parent_id"], row["contact"]) if row else None
