from __future__ import annotations

import httpx

from ..config import TAVILY_URL
from ..models import SearchHit
from .base import SearchError


class TavilyClient:
    """Tavily Search API. Only ever talks to Tavily; the URL under suspicion is searched *about*, never fetched."""

    name = "tavily"
    offline = False

    def __init__(self, api_key: str, *, base_url: str = TAVILY_URL, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20.0,
            transport=transport,
        )

    def search(self, query: str, *, topic: str = "general", max_results: int = 5) -> list[SearchHit]:
        payload = {
            "query": query,
            "topic": topic,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
        }
        try:
            resp = self._client.post("/search", json=payload)
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchError(f"Tavily search failed: {exc}") from exc
        return [
            SearchHit(title=r.get("title") or "", url=r["url"], content=r.get("content") or "")
            for r in results
            if r.get("url")
        ]
