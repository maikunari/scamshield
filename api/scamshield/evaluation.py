"""Eval over the seed set: run every message through the pipeline and score the label against gold."""

from __future__ import annotations

from dataclasses import dataclass, field

from .memory import MemoryStore
from .models import AnalysisResult
from .pipeline import Pipeline
from .runtime import load_expected, load_profile, load_seed_messages

LABELS = ["red", "yellow", "green"]


@dataclass
class EvalReport:
    rows: list[tuple[str, str, str, AnalysisResult]] = field(default_factory=list)  # (id, gold, predicted, result)
    type_hits: int = 0
    type_total: int = 0

    @property
    def accuracy(self) -> float:
        return sum(g == p for _, g, p, _ in self.rows) / len(self.rows) if self.rows else 0.0

    def confusion(self) -> dict[str, dict[str, int]]:
        m = {g: {p: 0 for p in LABELS} for g in LABELS}
        for _, g, p, _ in self.rows:
            m[g][p] += 1
        return m

    def per_class(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        conf = self.confusion()
        for c in LABELS:
            tp = conf[c][c]
            fp = sum(conf[g][c] for g in LABELS if g != c)
            fn = sum(conf[c][p] for p in LABELS if p != c)
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            out[c] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn}
        return out

    @property
    def dangerous_misses(self) -> list[str]:
        """Gold 🔴 predicted 🟢: the failure that matters most."""
        return [i for i, g, p, _ in self.rows if g == "red" and p == "green"]

    @property
    def totals(self) -> dict[str, float]:
        traces = [r.trace for *_, r in self.rows]
        return {
            "tokens": sum(t.total_tokens for t in traces),
            "cost_usd": sum(t.total_cost_usd for t in traces),
            "latency_ms": sum(t.total_latency_ms for t in traces),
            "model_calls": sum(1 for t in traces for s in t.steps if s.kind == "model"),
            "search_calls": sum(1 for t in traces for s in t.steps if s.kind == "tool" and "cache hit" not in s.detail),
        }


def run_eval(pipeline: Pipeline) -> EvalReport:
    """Runs against a fresh in-memory store (so the 24h search cache starts empty) with the seed profile."""
    settings = pipeline.settings
    profile = load_profile(settings.seed_dir)
    expected = load_expected(settings.seed_dir)
    pipeline.use_store(MemoryStore(":memory:"))
    pipeline.store.save_profile(profile)
    report = EvalReport()
    for mid, raw in load_seed_messages(settings.seed_dir):
        result = pipeline.analyze(raw, profile.id)
        gold = expected[mid]
        report.rows.append((mid, gold["label"], result.verdict.label, result))
        if gold["label"] == "red" and "scam_type" in gold:
            report.type_total += 1
            report.type_hits += result.triage.scam_type == gold["scam_type"]
    return report


def format_markdown(report: EvalReport) -> str:
    lines = ["| class | precision | recall | F1 | support |", "|---|---|---|---|---|"]
    names = {"red": "🔴 scam", "yellow": "🟡 careful", "green": "🟢 safe"}
    for c, m in report.per_class().items():
        lines.append(f"| {names[c]} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} | {int(m['support'])} |")
    lines.append(f"| **accuracy** | | | **{report.accuracy:.2f}** | {len(report.rows)} |")
    return "\n".join(lines)


def format_text(report: EvalReport) -> str:
    out = ["id    gold    pred    score  type                    detail"]
    for mid, gold, pred, r in report.rows:
        mark = "ok " if gold == pred else "MISS"
        out.append(
            f"{mid:<5} {gold:<7} {pred:<7} {r.verdict.score:>5}  {(r.triage.scam_type or '-'):<22} {mark} {r.verdict.evidence_note}"
        )
    out += ["", format_markdown(report), "", "confusion (rows=gold, cols=pred):"]
    conf = report.confusion()
    out.append("          " + "  ".join(f"{p:>6}" for p in LABELS))
    for g in LABELS:
        out.append(f"  {g:<7} " + "  ".join(f"{conf[g][p]:>6}" for p in LABELS))
    out.append(f"\n🔴 predicted 🟢 (dangerous misses): {report.dangerous_misses or 'none'}")
    if report.type_total:
        out.append(f"scam_type agreement on gold 🔴: {report.type_hits}/{report.type_total}")
    t = report.totals
    out.append(
        f"totals: {int(t['model_calls'])} model calls, {int(t['search_calls'])} live searches, {int(t['tokens'])} tokens, "
        f"~${t['cost_usd']:.4f} est. cost, {t['latency_ms']:.0f} ms summed step latency"
    )
    return "\n".join(out)
