/** Family dashboard API client and types. Mirrors api/scamshield/family.py, models.py and main.py. */
import type { Label } from "./types";

export class FamilyError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "FamilyError";
  }
}

export interface Delivery {
  contact: string;
  channel: "email" | "telegram" | "all" | string;
  status: "sent" | "recorded" | "skipped" | "failed" | "rate_limited" | string;
  target: string;
  detail: string;
  at: string;
}

export interface AlertState {
  id: string;
  created_at: string;
  acked_at: string | null;
  acked_by: string | null;
  acked_via: string | null;
  deliveries: Delivery[];
}

export interface TraceTotals {
  model_calls: number;
  models: string[];
  tokens: number;
  latency_ms: number;
  cost_usd: number;
}

/** One row of GET /parents/{id}/checks. */
export interface CheckSummary {
  id: string;
  created_at: string;
  label: Label;
  score: number;
  channel: string;
  sender: string | null;
  subject: string | null;
  snippet: string;
  body_kept: boolean;
  scam_type: string | null;
  summary: string;
  contact: { status: "trusted" | "known_service" | "unknown"; name: string | null };
  offline: boolean;
  demo: boolean;
  degraded: boolean;
  trace: TraceTotals;
  alert: AlertState | null;
}

export interface TraceStep {
  name: string;
  kind: "model" | "tool" | "local";
  tier: string | null;
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  cost_usd: number;
  detail: string;
  error: string | null;
}

export interface Lookup {
  skill: "check_phone" | "check_url" | "check_phrase";
  value: string;
  query: string;
  hits: { title: string; url: string; content: string }[];
  cached: boolean;
  skipped: string | null;
}

/** GET /results/{id}: the full stored result. */
export interface FullResult {
  id: string;
  created_at: string;
  message: { channel: string; sender: string | null; subject: string | null; body: string; urls: string[]; phones: string[] };
  triage: {
    signals: Record<string, boolean | string>;
    risk: string;
    scam_type: string | null;
    summary: string;
    language: string;
  };
  contact: { status: "trusted" | "known_service" | "unknown"; name: string | null; relationship: string | null; reason: string; lookalike_of: string | null };
  lookups: Lookup[];
  verdict: {
    label: Label;
    score: number;
    red_flags: { text: string; evidence_url: string | null }[];
    safe_action: string;
    family_points: string[];
    evidence_note: string;
    degraded: boolean;
  };
  trace: { steps: TraceStep[] };
  offline: boolean;
  budget_mode: string;
  cached_from: string | null;
  demo: boolean;
}

export interface Contact {
  name: string;
  relationship: string;
  phones: string[];
  emails: string[];
}

export interface KnownService {
  name: string;
  kind: string;
  domains: string[];
  phones: string[];
  senders: string[];
}

export interface Profile {
  id: string;
  name: string;
  languages: string[];
  trusted_contacts: Contact[];
  known_services: KnownService[];
  store_green_bodies: boolean;
}

export interface FamilyInfo {
  parent: { id: string; name: string };
  contacts: { name: string; relationship: string; emails: string[]; telegram_linked: boolean }[];
  telegram_configured: boolean;
  email_configured: boolean;
}

export interface TelegramLink {
  contact: string;
  code: string;
  url: string | null;
  bot_configured: boolean;
}

export interface Gate {
  demo_load: boolean;
  demo_passphrase: string | null;
}

export interface Session {
  parent_id: string;
  name: string;
}

export interface DemoLoaded {
  parent_id: string;
  name: string;
  checks: number;
  alerts: number;
  passphrase: string | null;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { ...(init.body ? { "content-type": "application/json" } : {}), ...init.headers },
      credentials: "same-origin",
    });
  } catch {
    throw new FamilyError("ScamShield could not be reached. Check the connection and try again.");
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new FamilyError(detail || `ScamShield answered ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) => call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const familyApi = {
  gate: () => call<Gate>("/api/family/gate"),
  session: () => call<Session>("/api/family/session"),
  login: (passphrase: string) => post<Session & { token: string }>("/api/family/login", { passphrase }),
  logout: () => post<{ ok: boolean }>("/api/family/logout"),
  loadDemo: () => post<DemoLoaded>("/api/demo/load"),
  checks: (parent: string) => call<CheckSummary[]>(`/parents/${encodeURIComponent(parent)}/checks?limit=50`),
  result: (id: string) => call<FullResult>(`/results/${encodeURIComponent(id)}`),
  ack: (parent: string, alertId: string) =>
    post<AlertState & { newly: boolean }>(`/parents/${encodeURIComponent(parent)}/alerts/${encodeURIComponent(alertId)}/ack`),
  profile: (parent: string) => call<Profile>(`/parents/${encodeURIComponent(parent)}/profile`),
  saveProfile: (parent: string, profile: Profile) =>
    call<Profile>(`/parents/${encodeURIComponent(parent)}/profile`, { method: "PUT", body: JSON.stringify(profile) }),
  family: (parent: string) => call<FamilyInfo>(`/parents/${encodeURIComponent(parent)}/family`),
  telegramLink: (parent: string, contact: string) => post<TelegramLink>(`/parents/${encodeURIComponent(parent)}/telegram-link`, { contact }),
};
