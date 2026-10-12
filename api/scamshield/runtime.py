"""Wiring: build a Pipeline from settings, and load the seed profile/messages."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .llm import build_llm_client
from .memory import MemoryStore, Profile
from .models import RawMessage
from .pipeline import Pipeline
from .search import build_search_client


def build_pipeline(settings: Settings | None = None, store: MemoryStore | None = None) -> Pipeline:
    settings = settings or Settings.from_env()
    store = store or MemoryStore(settings.db_path)
    return Pipeline(settings, build_llm_client(settings), build_search_client(settings), store)


def load_profile(seed_dir: Path) -> Profile:
    return Profile.model_validate_json((seed_dir / "profile.json").read_text(encoding="utf-8"))


def load_seed_messages(seed_dir: Path) -> list[tuple[str, RawMessage]]:
    """Seed messages as ``(id, RawMessage)`` in file order."""
    out: list[tuple[str, RawMessage]] = []
    for line in (seed_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out.append((row["id"], RawMessage(**{k: v for k, v in row.items() if k not in ("id", "lang")})))
    return out


def load_expected(seed_dir: Path) -> dict[str, dict]:
    return json.loads((seed_dir / "expected.json").read_text(encoding="utf-8"))
