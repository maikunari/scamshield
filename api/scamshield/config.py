"""Runtime configuration, read from the environment.

External calls (Token Factory, Tavily) are selected per client: a missing key, or
``SCAMSHIELD_OFFLINE=1``, selects the deterministic fake for that client so the whole
pipeline runs without network access.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"  # unverified, override with NEBIUS_BASE_URL
TAVILY_URL = "https://api.tavily.com"

TIERS = ("fast", "mid", "reason")
TIER_ENV = {"fast": "MODEL_FAST", "mid": "MODEL_MID", "reason": "MODEL_REASON"}

# USD per 1M tokens (input, output). Placeholder estimates until docs/models.md is generated from
# the real /models listing; override per tier with PRICE_<TIER>_IN / PRICE_<TIER>_OUT.
DEFAULT_PRICES = {
    "fast": (0.06, 0.24),
    "mid": (0.15, 0.60),
    "reason": (0.60, 1.80),
}
DEFAULT_TAVILY_COST = 0.008  # USD per live search call (placeholder, TAVILY_COST_PER_CALL)

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str | None = None
    nebius_base_url: str = DEFAULT_BASE_URL
    models: dict[str, str | None] = field(default_factory=dict)
    prices: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    tavily_api_key: str | None = None
    tavily_cost_per_call: float = DEFAULT_TAVILY_COST
    force_offline: bool = False
    db_path: str = "scamshield.db"
    seed_dir: Path = REPO_ROOT / "seed"
    cache_ttl_seconds: int = 24 * 3600
    max_search_calls: int = 3

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        prices = dict(DEFAULT_PRICES)
        for tier in TIERS:
            pin = e.get(f"PRICE_{tier.upper()}_IN")
            pout = e.get(f"PRICE_{tier.upper()}_OUT")
            if pin or pout:
                base = prices[tier]
                prices[tier] = (float(pin) if pin else base[0], float(pout) if pout else base[1])
        return cls(
            nebius_api_key=e.get("NEBIUS_API_KEY") or None,
            nebius_base_url=e.get("NEBIUS_BASE_URL") or DEFAULT_BASE_URL,
            models={t: e.get(TIER_ENV[t]) or None for t in TIERS},
            prices=prices,
            tavily_api_key=e.get("TAVILY_API_KEY") or None,
            tavily_cost_per_call=float(e.get("TAVILY_COST_PER_CALL") or DEFAULT_TAVILY_COST),
            force_offline=_truthy(e.get("SCAMSHIELD_OFFLINE")),
            db_path=e.get("SCAMSHIELD_DB") or "scamshield.db",
            seed_dir=Path(e.get("SCAMSHIELD_SEED_DIR") or REPO_ROOT / "seed"),
        )

    @property
    def llm_offline(self) -> bool:
        return self.force_offline or not self.nebius_api_key

    @property
    def search_offline(self) -> bool:
        return self.force_offline or not self.tavily_api_key

    def model_for(self, tier: str) -> str:
        """Model id for a tier. Offline, the fake reports ``fake/<tier>``."""
        model = self.models.get(tier)
        if model:
            return model
        if self.llm_offline:
            return f"fake/{tier}"
        raise ConfigError(
            f"{TIER_ENV[tier]} is not set. Run scripts/discover_models.py to list the models "
            f"available on Token Factory, then export {TIER_ENV[tier]}=<model id>."
        )
