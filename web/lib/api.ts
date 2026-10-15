import { parseSse } from "./progress";
import type { CheckEvent, CheckRequest, CheckResponse } from "./types";

export class CheckError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "CheckError";
  }
}

const TIMEOUT_MS = 45_000; // Ultra verdicts can take a while on a live key

/** POST the message to the core API. Same origin in production; proxied by `next dev` locally. */
export async function checkMessage(req: CheckRequest, signal?: AbortSignal): Promise<CheckResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  signal?.addEventListener("abort", () => controller.abort(), { once: true });
  try {
    const res = await fetch("/api/check", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    if (!res.ok) throw new CheckError(`ScamShield answered ${res.status}`, res.status);
    return (await res.json()) as CheckResponse;
  } catch (err) {
    if (err instanceof CheckError) throw err;
    throw new CheckError(err instanceof Error ? err.message : "network error");
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The same check with live progress: `onEvent` gets each step (and an early answer for obvious cases) as the
 * server sends it; the promise resolves with the final answer.
 */
export async function checkMessageLive(
  req: CheckRequest,
  onEvent: (e: CheckEvent) => void,
  signal?: AbortSignal,
): Promise<CheckResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  signal?.addEventListener("abort", () => controller.abort(), { once: true });
  try {
    const res = await fetch("/api/check/stream", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "text/event-stream" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    if (!res.ok) throw new CheckError(`ScamShield answered ${res.status}`, res.status);
    let buffer = "";
    const handle = (chunk: string): CheckResponse | null => {
      const { events, rest } = parseSse(buffer + chunk);
      buffer = rest;
      for (const e of events) {
        if (e.type === "result") return e.result;
        if (e.type === "error") throw new CheckError("the check failed", 500);
        onEvent(e);
      }
      return null;
    };
    if (res.body) {
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const result = handle(value);
        if (result) {
          void reader.cancel();
          return result;
        }
      }
    } else {
      const result = handle(await res.text());
      if (result) return result;
    }
    const result = handle("\n\n");
    if (result) return result;
    throw new CheckError("the answer was cut off", 502);
  } catch (err) {
    if (err instanceof CheckError) throw err;
    throw new CheckError(err instanceof Error ? err.message : "network error");
  } finally {
    clearTimeout(timer);
  }
}

/** Delete everything stored for this person: messages, answers, family alerts and what ScamShield remembers. */
export async function deleteMyData(signal?: AbortSignal): Promise<void> {
  try {
    const res = await fetch("/api/my-data?confirm=yes", { method: "DELETE", signal });
    if (!res.ok) throw new CheckError(`ScamShield answered ${res.status}`, res.status);
  } catch (err) {
    if (err instanceof CheckError) throw err;
    throw new CheckError(err instanceof Error ? err.message : "network error");
  }
}
