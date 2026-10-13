"""Runtime configuration, read from the environment.

External calls (Token Factory, Tavily) are selected per client: a missing key, or
``SCAMSHIELD_OFFLINE=1``, selects the deterministic fake for that client so the whole
pipeline runs without network access.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"  # confirmed in the Token Factory quickstart
TAVILY_URL = "https://api.tavily.com"

TIERS = ("fast", "mid", "reason", "long")
TIER_ENV = {"fast": "MODEL_FAST", "mid": "MODEL_MID", "reason": "MODEL_REASON", "long": "MODEL_LONG"}

# Default model ids, exactly as listed in the Token Factory model catalog (the casing there is inconsistent, so
# every comparison goes through ``model_key``). ``long`` is Nemotron 3.5 Lightning, reserved for the digest.
DEFAULT_MODELS = {
    "fast": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "mid": "nvidia/nemotron-3-super-120b-a12b",
    "reason": "nvidia/Nemotron-3-Ultra-550b-a55b",
    "long": "nvidia/Nemotron-3_5-Lightning",
}

# USD per 1M tokens (input, output), from the Token Factory model catalog (2026-10-13), keyed by lowercase model id.
MODEL_PRICES = {
    "nvidia/nvidia-nemotron-3-nano-30b-a3b": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/nemotron-3-ultra-550b-a55b": (1.00, 3.00),
    "nvidia/nemotron-3_5-lightning": (0.06, 0.24),
}
# Fallback per tier for ids that are not in the table (including the offline ``fake/<tier>`` ids).
# Override per tier with PRICE_<TIER>_IN / PRICE_<TIER>_OUT.
DEFAULT_PRICES = {tier: MODEL_PRICES[DEFAULT_MODELS[tier].lower()] for tier in TIERS}
DEFAULT_TAVILY_COST = 0.008  # USD per Tavily credit (pay-as-you-go list price, TAVILY_COST_PER_CALL)
TAVILY_EXTRACT_CREDITS_PER_URL = 0.2  # extract bills 1 credit per 5 successful URLs

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


def model_key(model_id: str) -> str:
    """Canonical form for comparing model ids: Token Factory's catalog mixes casing."""
    return model_id.strip().lower()


def resolve_model_id(wanted: str, available: list[str]) -> str | None:
    """The catalog's spelling of ``wanted`` (case-insensitive), or None when it is not listed."""
    by_key = {model_key(m): m for m in available}
    return by_key.get(model_key(wanted))


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str | None = None
    nebius_base_url: str = DEFAULT_BASE_URL
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    prices: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    price_overrides: frozenset[str] = frozenset()  # tiers whose price came from PRICE_<TIER>_* env vars
    tavily_api_key: str | None = None
    tavily_cost_per_call: float = DEFAULT_TAVILY_COST
    force_offline: bool = False
    db_path: str = "scamshield.db"
    seed_dir: Path = REPO_ROOT / "seed"
    cache_ttl_seconds: int = 24 * 3600
    verdict_cache_ttl_seconds: int = 24 * 3600
    max_search_calls: int = 3
    max_extract_calls: int = 1
    # Budget guard (see budget.py). Spend is in USD as estimated in the trace.
    daily_ultra_call_cap: int = 300
    daily_ultra_usd_cap: float = 10.0
    total_spend_cap_usd: float = 40.0
    degrade_at_fraction: float = 0.8  # Nano-only once this share of the total cap is spent
    kill_switch: bool = False
    kill_file: str | None = None  # the kill switch is also on while this file exists
    # Per-IP limit on the analyze endpoint; 0 disables it.
    rate_limit_per_window: int = 10
    rate_limit_window_seconds: int = 60
    trust_forwarded_for: bool = False
    demo_parent_id: str = "michiko"  # parent the public /api/check endpoint analyses for
    web_dir: Path = REPO_ROOT / "web" / "out"  # Next.js static export, served at / when present
    # Family alerts (alerts.py). A channel without its credentials records the alert instead of sending it.
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    resend_api_key: str | None = None
    alert_email_from: str | None = None
    public_base_url: str | None = None  # origin the family reaches this app on; links and the webhook use it
    alert_max_per_contact: int = 5  # alerts one family contact receives per window (0 disables the limit)
    alert_window_seconds: int = 3600

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        prices = dict(DEFAULT_PRICES)
        overrides: set[str] = set()
        for tier in TIERS:
            pin = e.get(f"PRICE_{tier.upper()}_IN")
            pout = e.get(f"PRICE_{tier.upper()}_OUT")
            if pin or pout:
                base = prices[tier]
                prices[tier] = (float(pin) if pin else base[0], float(pout) if pout else base[1])
                overrides.add(tier)
        defaults = cls()
        return cls(
            nebius_api_key=e.get("NEBIUS_API_KEY") or None,
            nebius_base_url=e.get("NEBIUS_BASE_URL") or DEFAULT_BASE_URL,
            models={t: e.get(TIER_ENV[t]) or DEFAULT_MODELS[t] for t in TIERS},
            prices=prices,
            price_overrides=frozenset(overrides),
            tavily_api_key=e.get("TAVILY_API_KEY") or None,
            tavily_cost_per_call=float(e.get("TAVILY_COST_PER_CALL") or DEFAULT_TAVILY_COST),
            force_offline=_truthy(e.get("SCAMSHIELD_OFFLINE")),
            db_path=e.get("SCAMSHIELD_DB") or "scamshield.db",
            seed_dir=Path(e.get("SCAMSHIELD_SEED_DIR") or REPO_ROOT / "seed"),
            daily_ultra_call_cap=int(e.get("BUDGET_DAILY_ULTRA_CALLS") or defaults.daily_ultra_call_cap),
            daily_ultra_usd_cap=float(e.get("BUDGET_DAILY_ULTRA_USD") or defaults.daily_ultra_usd_cap),
            total_spend_cap_usd=float(e.get("BUDGET_TOTAL_USD") or defaults.total_spend_cap_usd),
            kill_switch=_truthy(e.get("BUDGET_KILL_SWITCH")),
            kill_file=e.get("BUDGET_KILL_FILE") or None,
            rate_limit_per_window=int(e.get("RATE_LIMIT_PER_MINUTE") or defaults.rate_limit_per_window),
            trust_forwarded_for=_truthy(e.get("TRUST_FORWARDED_FOR")),
            demo_parent_id=e.get("SCAMSHIELD_DEMO_PARENT") or "michiko",
            web_dir=Path(e.get("SCAMSHIELD_WEB_DIR") or REPO_ROOT / "web" / "out"),
            telegram_bot_token=e.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_webhook_secret=e.get("TELEGRAM_WEBHOOK_SECRET") or None,
            resend_api_key=e.get("RESEND_API_KEY") or None,
            alert_email_from=e.get("ALERT_EMAIL_FROM") or None,
            public_base_url=(e.get("PUBLIC_BASE_URL") or "").strip().rstrip("/") or None,
            alert_max_per_contact=int(e.get("ALERT_MAX_PER_CONTACT_PER_HOUR") or defaults.alert_max_per_contact),
        )

    @property
    def llm_offline(self) -> bool:
        return self.force_offline or not self.nebius_api_key

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token) and not self.force_offline

    @property
    def email_configured(self) -> bool:
        return bool(self.resend_api_key and self.alert_email_from) and not self.force_offline

    @property
    def search_offline(self) -> bool:
        return self.force_offline or not self.tavily_api_key

    def model_for(self, tier: str) -> str:
        """Model id for a tier. Offline, the fake reports ``fake/<tier>``."""
        if self.llm_offline:
            return f"fake/{tier}"
        return self.models[tier]

    def price_for(self, tier: str, model: str) -> tuple[float, float]:
        """USD per 1M (input, output) tokens. An explicit PRICE_<TIER> override wins, then the catalog price of
        ``model`` (matched case-insensitively), then the tier default."""
        if tier in self.price_overrides:
            return self.prices[tier]
        return MODEL_PRICES.get(model_key(model), self.prices[tier])
