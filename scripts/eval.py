#!/usr/bin/env python
"""Run the seed set through the pipeline and print per-class precision/recall.

    uv run --project api python scripts/eval.py            # offline fakes unless keys are set
    SCAMSHIELD_OFFLINE=1 uv run --project api python scripts/eval.py --markdown
"""

import argparse
import sys

from scamshield.config import ConfigError, Settings
from scamshield.evaluation import format_markdown, format_text, run_eval
from scamshield.runtime import build_pipeline


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--markdown", action="store_true", help="print only the markdown table (for the README)")
    ap.add_argument("--fail-on-red-as-green", action="store_true", help="exit 1 if any gold-red message is predicted green")
    args = ap.parse_args()
    settings = Settings.from_env()
    try:
        pipeline = build_pipeline(settings)
        mode = "offline fakes" if pipeline.llm.offline or pipeline.search.offline else "live Token Factory + Tavily"
        print(f"# eval mode: {mode} (llm={pipeline.llm.name}, search={pipeline.search.name})", file=sys.stderr)
        report = run_eval(pipeline)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    print(format_markdown(report) if args.markdown else format_text(report))
    return 1 if args.fail_on_red_as_green and report.dangerous_misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
