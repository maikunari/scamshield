from __future__ import annotations

from typing import Protocol

from ..models import SearchHit


class SearchError(RuntimeError):
    pass


class SearchClient(Protocol):
    """Anything that can run a web search. Implementations never fetch the page being asked about."""

    name: str
    offline: bool

    def search(self, query: str, *, topic: str = "general", max_results: int = 5) -> list[SearchHit]: ...
