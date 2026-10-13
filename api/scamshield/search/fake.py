from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import SearchHit


def _squash(text: str) -> str:
    return re.sub(r"[\s\-().]", "", text.lower())


class FakeSearchClient:
    """Offline stand-in: replays recorded results from a fixtures file.

    Fixture shape: ``[{"match": "<substring>", "results": [{"title", "url", "content"}]}]``. A fixture applies
    when ``match`` appears in the query (ignoring spaces and punctuation). Anything else returns no results,
    exactly as Tavily does for an identifier nobody has reported.
    """

    name = "fake-tavily"
    offline = True

    def __init__(self, fixtures: list[dict] | None = None):
        self._fixtures = fixtures or []
        self.calls: list[str] = []
        self.extract_calls: list[str] = []

    @classmethod
    def from_file(cls, path: Path) -> FakeSearchClient:
        if not path.exists():
            return cls([])
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def search(self, query: str, *, topic: str = "general", max_results: int = 5) -> list[SearchHit]:
        self.calls.append(query)
        q = _squash(query)
        hits: list[SearchHit] = []
        for fx in self._fixtures:
            if _squash(fx["match"]) in q:
                hits += [SearchHit(**r) for r in fx["results"]]
        return hits[:max_results]

    def extract(self, url: str, *, max_chars: int = 1500) -> str:
        """The recorded snippet for that URL, standing in for the page text."""
        self.extract_calls.append(url)
        for fx in self._fixtures:
            for r in fx["results"]:
                if r["url"] == url:
                    return " ".join(r.get("content", "").split())[:max_chars]
        return ""
