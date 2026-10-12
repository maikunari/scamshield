"""Search clients. ``build_search_client`` picks Tavily or the offline fixture-backed fake."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .base import SearchClient, SearchError
from .fake import FakeSearchClient
from .tavily import TavilyClient

__all__ = ["SearchClient", "SearchError", "FakeSearchClient", "TavilyClient", "build_search_client"]


def build_search_client(settings: Settings, fixtures: Path | None = None) -> SearchClient:
    if settings.search_offline:
        return FakeSearchClient.from_file(fixtures or settings.seed_dir / "search_fixtures.json")
    return TavilyClient(settings.tavily_api_key or "")
