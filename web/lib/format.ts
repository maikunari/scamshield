import type { Label } from "./types";
import type { TraceStep } from "./family";

export const LABEL_WORD: Record<Label, string> = { red: "Scam", yellow: "Careful", green: "Safe" };

export function timeAgo(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d} day${d === 1 ? "" : "s"} ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function fullTime(iso: string): string {
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? "" : t.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function fmtSeconds(ms: number): string {
  if (ms < 1) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function fmtUsd(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return `$${usd.toFixed(usd < 0.01 ? 5 : 4)}`.replace(/0+$/, "").replace(/\.$/, "");
}

export function fmtNumber(n: number): string {
  return n.toLocaleString("en-US");
}

export type ModelFamily = "Nano" | "Super" | "Ultra" | "Lightning";

const TIER_FAMILY: Record<string, ModelFamily> = { fast: "Nano", mid: "Super", reason: "Ultra", long: "Lightning" };

/** Nemotron family name of a model step, from the model id (falling back to its routing tier). */
export function modelFamily(step: Pick<TraceStep, "model" | "tier">): ModelFamily | null {
  const id = (step.model ?? "").toLowerCase();
  if (id.includes("nano")) return "Nano";
  if (id.includes("super")) return "Super";
  if (id.includes("ultra")) return "Ultra";
  if (id.includes("lightning")) return "Lightning";
  return step.tier ? (TIER_FAMILY[step.tier] ?? null) : null;
}

export interface StepCopy {
  title: string;
  blurb: string;
}

const LOOKUP: Record<string, string> = {
  check_phone: "Look up the phone number",
  check_url: "Look up the link",
  check_phrase: "Look up the wording",
  extract: "Read the best source page",
};

/** Plain-words title and what-it-does line for a trace step. */
export function stepCopy(step: TraceStep, parentFirstName: string): StepCopy {
  const family = modelFamily(step);
  const who = family ? `Nemotron ${family}` : "The model";
  switch (step.name) {
    case "normalize":
      return { title: "Read the message", blurb: "Pulls out the sender, links and phone numbers." };
    case "redact":
      return { title: "Hide private details", blurb: "Card numbers and similar are removed before any model sees the text." };
    case "triage":
      return { title: "Triage", blurb: `${who} reads the message and marks the scam signals: urgency, money, impersonation.` };
    case "memory":
      return { title: `Check ${parentFirstName}'s memory`, blurb: "Compares the sender with her trusted contacts and the services she uses." };
    case "quick_check":
      return { title: "Early answer", blurb: "A well-known scam script or a trusted contact, shown at once while the full check ran." };
    case "plan": // older checks: a model chose the lookups before they became a fixed plan
      return { title: "Plan the evidence search", blurb: `${who} chooses what is worth looking up.` };
    case "verdict":
      return { title: "Decide and explain", blurb: `${who} weighs everything and writes the verdict, the red flags and the notes for the family.` };
    case "guardrails":
      return { title: "Safety rules", blurb: "Fixed rules that memory and failed steps can never be talked out of." };
    case "fast_path":
      return { title: "Fast path", blurb: "A known, plainly safe sender: no search and no big model needed." };
    case "budget":
      return { title: "Budget guard", blurb: "Spending limits changed how this check ran." };
    case "verdict_cache":
      return { title: "Replayed an earlier verdict", blurb: "Same message, same memory: nothing was called again." };
    default: {
      if (step.name.startsWith("tavily:")) {
        const skill = step.name.slice("tavily:".length);
        return { title: LOOKUP[skill] ?? "Search the web", blurb: "Tavily searches for public scam reports. The suspicious link itself is never opened." };
      }
      return { title: step.name.replace(/[_:]/g, " "), blurb: "" };
    }
  }
}

/** The query Tavily was asked, from a tool step's `detail` ("... | q=<query>"). */
export function stepQuery(step: TraceStep): string | null {
  const i = step.detail.indexOf("| q=");
  return i >= 0 ? step.detail.slice(i + 4).trim() : null;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

/** A sender for people: E.164 numbers are shown the way they are dialled; anything else is left alone. */
export function prettySender(sender: string | null | undefined): string {
  const s = (sender ?? "").trim();
  if (!s) return "Unknown sender";
  let m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(s);
  if (m) return `(${m[1]}) ${m[2]}-${m[3]}`;
  m = /^\+81(\d{2,3})(\d{4})(\d{4})$/.exec(s);
  if (m) return `0${m[1]}-${m[2]}-${m[3]}`;
  return s;
}
