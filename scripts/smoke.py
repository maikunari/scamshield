#!/usr/bin/env python
"""Live smoke test against the real Token Factory and Tavily (needs both keys; exits 2 without them).

    NEBIUS_API_KEY=... TAVILY_API_KEY=... uv run --project api python scripts/smoke.py
    ... python scripts/smoke.py --seed-id m10 --json     # another seed message, full result as JSON

Checks that the default Nemotron ids are listed (case-insensitive), that Nano, Super and Ultra each honour
response_format=json_schema, and that one seed message runs end to end (triage, plan, Tavily, verdict) with tokens and
cost in the trace. Costs a few cents. Uses a throwaway in-memory database, so no cache or budget state is touched.
"""

import argparse
import sys

from scamshield.config import Settings
from scamshield.smoke import SEED_ID, SmokeUnavailable, format_checks, run_smoke


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed-id", default=SEED_ID)
    ap.add_argument("--json", action="store_true", help="also print the full AnalysisResult")
    args = ap.parse_args()
    try:
        checks, result = run_smoke(Settings.from_env(), seed_id=args.seed_id)
    except SmokeUnavailable as exc:
        print(f"smoke test not run: {exc}", file=sys.stderr)
        return 2
    print(format_checks(checks))
    if result is not None:
        from scamshield.cli import format_result

        print()
        print(result.model_dump_json(indent=2) if args.json else format_result(result))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
