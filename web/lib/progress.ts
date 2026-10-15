import type { CheckEvent, Label } from "./types";

/** Split a server-sent-events buffer into its complete events; the unfinished tail is returned as `rest`. */
export function parseSse(buffer: string): { events: CheckEvent[]; rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  const events: CheckEvent[] = [];
  for (const block of blocks) {
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as CheckEvent);
    } catch {
      // A garbled event is skipped; the result event decides the outcome.
    }
  }
  return { events, rest };
}

export interface ProgressLine {
  key: "reading" | "searching" | "found" | "deciding";
  text: string;
  done: boolean;
}

/** What the parent sees while the check runs: the steps so far and, for an obvious case, an early answer. */
export interface Progress {
  lines: ProgressLine[];
  quick: { label: Label; reason: string } | null;
}

export const START: Progress = { lines: [{ key: "reading", text: "Reading the message", done: false }], quick: null };

function reportsFound(count: number): string {
  if (count <= 0) return "No public reports found";
  return count === 1 ? "1 public report found" : `${count} public reports found`;
}

function push(p: Progress, line: ProgressLine): Progress {
  return { ...p, lines: [...p.lines.map((l) => ({ ...l, done: true })), line] };
}

export function advance(p: Progress, e: CheckEvent): Progress {
  if (e.type === "quick") return { ...p, quick: { label: e.label, reason: e.reason } };
  if (e.type !== "step" || p.lines.some((l) => l.key === e.step)) return p;
  switch (e.step) {
    case "searching":
      return push(p, { key: "searching", text: "Checking public scam reports", done: false });
    case "found":
      return push(p, { key: "found", text: reportsFound(e.count), done: true });
    case "deciding":
      return push(p, { key: "deciding", text: "Making the verdict", done: false });
    default:
      return p;
  }
}
