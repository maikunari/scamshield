"""Spend guard for the hosted demo: a daily Ultra cap, a total spend cap, a Nano-only degraded mode and a kill switch.

The ledger lives in the same SQLite database as everything else (``MemoryStore.spend_*``), so the caps survive
restarts. Modes, worst first:

- ``killed``    BUDGET_KILL_SWITCH=1, the kill file exists, or ``Budget.kill()``: no paid call of any kind.
- ``exhausted`` total spend reached BUDGET_TOTAL_USD: no paid call of any kind.
- ``degraded``  the daily Ultra call or dollar cap is hit, or 80% of the total cap is spent: every model step runs
                on the fast tier (Nano). Searches still run.
- ``normal``    everything as routed.

Cached verdicts and cached searches cost nothing, so they are served in every mode. When paid calls are refused the
pipeline falls back to its rule-based "could not fully check" verdict, which asks the family to look.
Only live (non-fake) clients are booked, so offline runs never trip a cap.
"""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass

from .config import Settings
from .memory import MemoryStore

NORMAL, DEGRADED, EXHAUSTED, KILLED = "normal", "degraded", "exhausted", "killed"


class BudgetBlocked(RuntimeError):
    """A paid call was refused by the budget guard."""


@dataclass
class BudgetStatus:
    mode: str
    reason: str
    total_usd: float
    total_cap_usd: float
    ultra_calls_today: int
    ultra_calls_cap: int
    ultra_usd_today: float
    ultra_usd_cap: float

    def as_dict(self) -> dict:
        return asdict(self)


class Budget:
    def __init__(self, store: MemoryStore, settings: Settings):
        self.store = store
        self.settings = settings
        self._killed = False
        self._lock = threading.Lock()

    # --- control ---------------------------------------------------------------------------------
    def kill(self) -> None:
        """Flip the in-process kill switch (the env var and the kill file are re-read on every check)."""
        with self._lock:
            self._killed = True

    def revive(self) -> None:
        with self._lock:
            self._killed = False

    # --- state -----------------------------------------------------------------------------------
    def status(self) -> BudgetStatus:
        s = self.settings
        total = self.store.spend_total()
        calls, usd = self.store.spend_today("reason")
        mode, reason = NORMAL, "within budget"
        if self._killed or s.kill_switch or (s.kill_file and os.path.exists(s.kill_file)):
            mode, reason = KILLED, "kill switch is on"
        elif total >= s.total_spend_cap_usd:
            mode, reason = EXHAUSTED, f"total spend ${total:.2f} reached the ${s.total_spend_cap_usd:.2f} cap"
        elif calls >= s.daily_ultra_call_cap:
            mode, reason = DEGRADED, f"daily Ultra call cap ({s.daily_ultra_call_cap}) reached"
        elif usd >= s.daily_ultra_usd_cap:
            mode, reason = DEGRADED, f"daily Ultra spend cap (${s.daily_ultra_usd_cap:.2f}) reached"
        elif total >= s.degrade_at_fraction * s.total_spend_cap_usd:
            mode, reason = DEGRADED, f"{s.degrade_at_fraction:.0%} of the total spend cap used"
        return BudgetStatus(mode, reason, round(total, 6), s.total_spend_cap_usd, calls, s.daily_ultra_call_cap, round(usd, 6), s.daily_ultra_usd_cap)

    @property
    def mode(self) -> str:
        return self.status().mode

    def plan_tier(self, wanted: str, status: BudgetStatus | None = None) -> str:
        """The tier to actually call for ``wanted``. Raises ``BudgetBlocked`` when no paid call is allowed."""
        st = status or self.status()
        if st.mode in (KILLED, EXHAUSTED):
            raise BudgetBlocked(f"{st.mode}: {st.reason}")
        return "fast" if st.mode == DEGRADED else wanted

    def check_search(self) -> None:
        st = self.status()
        if st.mode in (KILLED, EXHAUSTED):
            raise BudgetBlocked(f"{st.mode}: {st.reason}")

    # --- ledger ----------------------------------------------------------------------------------
    def record(self, kind: str, tier: str, usd: float) -> None:
        self.store.spend_add(kind, tier, usd)
