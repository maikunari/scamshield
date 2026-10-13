import type { CheckRequest, CheckResponse } from "./types";

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
