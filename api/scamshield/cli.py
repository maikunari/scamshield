"""``scamshield`` CLI: analyze one message, or a seed message, with the configured (or offline) clients."""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, Settings
from .memory import MemoryStore
from .models import AnalysisResult, RawMessage
from .runtime import build_pipeline, load_profile, load_seed_messages

ICON = {"green": "🟢 Safe", "yellow": "🟡 Careful", "red": "🔴 Scam: do not reply, pay, or call"}


def format_result(r: AnalysisResult) -> str:
    v = r.verdict
    tags = ("  [offline fakes]" if r.offline else "") + (f"  [budget: {r.budget_mode}]" if r.budget_mode != "normal" else "")
    lines = [f"{ICON[v.label]}  (score {v.score}/100){tags}"]
    if r.cached_from:
        lines.append(f"  (replayed from cached analysis {r.cached_from}, no model or search call)")
    lines.append(f"  sender: {r.message.sender or 'unknown'}  ->  {r.contact.status}" + (f" ({r.contact.name})" if r.contact.name else ""))
    for f in v.red_flags:
        lines.append(f"  - {f.text}" + (f"\n      evidence: {f.evidence_url}" if f.evidence_url else ""))
    lines.append(f"  evidence: {v.evidence_note}")
    lines.append(f"  do this: {v.safe_action}")
    if v.family_script:
        lines.append(f"  call script: {v.family_script}")
    lines.append("  trace:")
    for s in r.trace.steps:
        who = f"{s.tier}:{s.model}" if s.model else s.kind
        lines.append(
            f"    {s.name:<22} {who:<28} {s.tokens_in:>5}+{s.tokens_out:<5} tok {s.latency_ms:>8.1f} ms ${s.cost_usd:.5f}"
            + (f"  ERROR {s.error}" if s.error else "")
        )
    t = r.trace
    lines.append(f"    {'total':<22} {'':<28} {t.total_tokens:>11} tok {t.total_latency_ms:>8.1f} ms ${t.total_cost_usd:.5f}")
    return "\n".join(lines)


def warm(settings: Settings, db_path: str) -> int:
    """Run the seed set once with the configured clients and pin each verdict in the verdict cache of ``db_path``."""
    store = MemoryStore(db_path)
    profile = load_profile(settings.seed_dir)
    store.save_profile(profile)
    try:
        pipeline = build_pipeline(settings, store)
        for mid, raw in load_seed_messages(settings.seed_dir):
            r = pipeline.analyze(raw, profile.id, use_cache=False, pin=True)
            pinned = store.verdict_cache_get(pipeline.cache_key(r.message, profile), 0) is not None
            note = "pinned" if pinned else f"NOT cached (budget={r.budget_mode}, degraded or errors)"
            print(f"{mid} {r.verdict.label:<6} {r.verdict.score:>3}  ${r.trace.total_cost_usd:.5f}  {note}")
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    print(f"pinned in {db_path}: replays of the seed messages now cost nothing")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scamshield")
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze", help="analyze one message")
    an.add_argument("--body", help="message text (default: read stdin)")
    an.add_argument("--sender")
    an.add_argument("--channel", default="paste", choices=["sms", "email", "voicemail", "paste"])
    an.add_argument("--seed-id", help="analyze a seed message by id instead of --body")
    an.add_argument("--no-profile", action="store_true", help="do not load the seed Michiko profile")
    an.add_argument("--json", action="store_true", help="print the full AnalysisResult as JSON")
    wm = sub.add_parser("warm", help="analyze every seed message and pin the verdicts so they are free to replay")
    wm.add_argument("--db", help="database to warm (default: SCAMSHIELD_DB)")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    if args.cmd == "warm":
        return warm(settings, args.db or settings.db_path)
    store = MemoryStore(":memory:")  # CLI runs are throwaway; the API uses SCAMSHIELD_DB
    profile = load_profile(settings.seed_dir)
    if not args.no_profile:
        store.save_profile(profile)
    try:
        pipeline = build_pipeline(settings, store)
        if args.seed_id:
            raw = dict(load_seed_messages(settings.seed_dir)).get(args.seed_id)
            if raw is None:
                ap.error(f"no seed message {args.seed_id!r}")
        else:
            raw = RawMessage(body=args.body or sys.stdin.read(), sender=args.sender, channel=args.channel)
        result = pipeline.analyze(raw, profile.id)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2) if args.json else format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
