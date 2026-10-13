"""Privacy controls: export everything stored for a parent, delete it, and optional retention.

The spend ledger (no PII) is neither exported nor deleted, so the budget caps keep holding."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from .alerts import alert_view
from .config import Settings
from .memory import MemoryStore

RETENTION_SWEEP_SECONDS = 3600


def export_parent(store: MemoryStore, parent_id: str) -> dict:
    """Everything stored for ``parent_id`` as plain JSON: profile, checks (with their traces), alerts (no ack
    tokens) and Telegram link status. Empty when nothing is stored."""
    profile = store.get_profile(parent_id)
    return {
        "parent_id": parent_id,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": profile.model_dump(mode="json") if profile else None,
        "checks": [r.model_dump(mode="json") for r in store.analyses_for_parent(parent_id)],
        "alerts": [alert_view(a) for a in store.alerts_for_parent(parent_id)],
        "telegram_links": store.telegram_links_for_parent(parent_id),
    }


class Retention:
    """Deletes checks older than ``settings.retention_days`` (0 = off). ``maybe_sweep`` is cheap to call on
    every request: it only touches the database once an hour."""

    def __init__(self, settings: Settings, store: MemoryStore):
        self.days = settings.retention_days
        self.store = store
        self._last = 0.0
        self._lock = threading.Lock()

    def sweep(self) -> dict[str, int]:
        if self.days <= 0:
            return {}
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days)
        return self.store.purge_older_than(cutoff.replace(microsecond=0))

    def maybe_sweep(self) -> None:
        if self.days <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._last and now - self._last < RETENTION_SWEEP_SECONDS:
                return
            self._last = now
        self.sweep()
