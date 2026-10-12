"""Reusable Tavily skills: ``check_phone``, ``check_url``, ``check_phrase``.

Each skill is a plain function ``(desk, value) -> Lookup``. The ``EvidenceDesk`` carries what they share:
the search client, the 24h cache, the per-message call budget and the trace. Results are filtered for
relevance so a search that only returned unrelated pages counts as "no public reports found" rather
than becoming fake evidence.
"""

from __future__ import annotations

import hashlib
import re
import time

from .config import Settings
from .memory import MemoryStore
from .models import Lookup, SearchHit, Trace, TraceStep
from .normalize import domain_of, phone_variants
from .search.base import SearchClient, SearchError

SCAM_TERMS = re.compile(r"scam|fraud|phish|complaint|warning|alert|advisory|spam|詐欺|フィッシング|注意", re.IGNORECASE)


class EvidenceDesk:
    def __init__(
        self,
        client: SearchClient,
        store: MemoryStore,
        settings: Settings,
        trace: Trace,
    ):
        self.client = client
        self.store = store
        self.settings = settings
        self.trace = trace
        self.live_calls = 0

    @property
    def budget_left(self) -> int:
        return self.settings.max_search_calls - self.live_calls

    def search(self, skill: str, query: str, topic: str) -> tuple[list[SearchHit], bool, str | None]:
        """Returns (hits, cached, skipped_reason). A cache hit is free; a live call spends budget."""
        key = hashlib.sha256(f"{topic}|{' '.join(query.lower().split())}".encode()).hexdigest()
        cached = self.store.cache_get(key, self.settings.cache_ttl_seconds)
        start = time.perf_counter()
        if cached is not None:
            hits = [SearchHit(**h) for h in cached]
            self._trace(skill, query, start, cost=0.0, detail=f"cache hit, {len(hits)} raw results")
            return hits, True, None
        if self.budget_left <= 0:
            self._trace(skill, query, start, cost=0.0, detail="skipped: search budget exhausted")
            return [], False, "search budget exhausted"
        self.live_calls += 1
        try:
            hits = self.client.search(query, topic=topic)
        except SearchError as exc:
            self._trace(skill, query, start, cost=0.0, detail="search failed", error=str(exc))
            return [], False, f"search failed: {exc}"
        self.store.cache_put(key, [h.model_dump() for h in hits])
        cost = 0.0 if self.client.offline else self.settings.tavily_cost_per_call
        self._trace(skill, query, start, cost=cost, detail=f"{self.client.name}: {len(hits)} raw results")
        return hits, False, None

    def _trace(self, skill: str, query: str, start: float, *, cost: float, detail: str, error: str | None = None):
        self.trace.steps.append(
            TraceStep(
                name=f"tavily:{skill}",
                kind="tool",
                latency_ms=round((time.perf_counter() - start) * 1000, 1),
                cost_usd=cost,
                detail=f"{detail} | q={query}",
                error=error,
            )
        )


def _mentions_any(hit: SearchHit, needles: list[str], *, digits_only: bool = False) -> bool:
    haystack = f"{hit.title} {hit.url} {hit.content}".lower()
    if digits_only:
        haystack = re.sub(r"\D", "", haystack)
    return any(n and n in haystack for n in needles)


def _run(desk: EvidenceDesk, skill: str, value: str, query: str, topic: str, relevant) -> Lookup:
    hits, cached, skipped = desk.search(skill, query, topic)
    kept = [h for h in hits if relevant(h)]
    return Lookup(
        skill=skill,  # type: ignore[arg-type]
        value=value,
        query=query,
        topic=topic,
        hits=kept,
        raw_hit_count=len(hits),
        cached=cached,
        skipped=skipped,
    )


def check_phone(desk: EvidenceDesk, phone: str) -> Lookup:
    """Reports about a phone number. Query: ``"<E.164>" OR "<local>" scam OR fraud OR complaint``."""
    variants = phone_variants(phone)
    query = " OR ".join(f'"{v}"' for v in variants) + " scam OR fraud OR complaint"
    national = re.sub(r"\D", "", phone)[-10:]  # compare on the last 10 digits, ignoring country code
    return _run(desk, "check_phone", phone, query, "general", lambda h: _mentions_any(h, [national], digits_only=True))


def check_url(desk: EvidenceDesk, url: str) -> Lookup:
    """Reports about a link's domain. The link itself is never fetched, only searched about."""
    domain = domain_of(url)
    query = f"{domain} scam phishing"
    return _run(desk, "check_url", url, query, "general", lambda h: _mentions_any(h, [domain]))


def check_phrase(desk: EvidenceDesk, phrase: str) -> Lookup:
    """Current campaigns and official warnings that use this wording (news topic)."""
    query = f'"{phrase}" scam'
    return _run(desk, "check_phrase", phrase, query, "news", lambda h: bool(SCAM_TERMS.search(f"{h.title} {h.content}")))


SKILLS = {"check_phone": check_phone, "check_url": check_url, "check_phrase": check_phrase}
