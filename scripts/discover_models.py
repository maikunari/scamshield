#!/usr/bin/env python
"""List the models available on Nebius Token Factory and write docs/models.md.

    NEBIUS_API_KEY=... uv run --project api python scripts/discover_models.py
    NEBIUS_BASE_URL=https://.../v1/ ...   # defaults to https://api.tokenfactory.nebius.com/v1/

Run this once the key is available. The defaults for MODEL_FAST / MODEL_MID / MODEL_REASON / MODEL_LONG are the Nemotron
ids from the Token Factory catalog; the table shows whether the account lists them (case-insensitive match).
"""

import argparse
import os
import sys
from pathlib import Path

import httpx
from scamshield.config import DEFAULT_BASE_URL
from scamshield.discovery import check_defaults, fetch_models, render_markdown

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "models.md")
    args = ap.parse_args()
    key = os.environ.get("NEBIUS_API_KEY")
    base = os.environ.get("NEBIUS_BASE_URL") or DEFAULT_BASE_URL
    if not key:
        print("NEBIUS_API_KEY is not set; nothing to discover. (Offline mode needs no models.)", file=sys.stderr)
        return 2
    try:
        models = fetch_models(base, key)
    except httpx.HTTPError as exc:
        print(f"GET {base.rstrip('/')}/models failed: {exc}\nCheck NEBIUS_BASE_URL and the key.", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(models, base), encoding="utf-8")
    print(f"wrote {args.out} ({len(models)} models)")
    missing = [(env, default) for env, default, listed in check_defaults(models) if not listed]
    for env, default in missing:
        print(f"warning: default {env}={default} is not in the listing; set {env} to one that is", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
