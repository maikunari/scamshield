/** Mirror of `CheckResponse` in api/scamshield/main.py. */
export type Label = "red" | "yellow" | "green";

export interface RedFlag {
  text: string;
  evidence_url: string | null;
}

export interface CheckResponse {
  id: string;
  label: Label;
  score: number;
  red_flags: RedFlag[];
  safe_action: string;
  evidence_note: string;
  contact: { status: "trusted" | "known_service" | "unknown"; name: string | null; relationship: string | null; reason: string };
  family: { name: string | null; notified: boolean };
  language: string;
  offline: boolean;
  degraded: boolean;
}

export interface CheckRequest {
  body: string;
  sender?: string | null;
  channel?: "sms" | "email" | "voicemail" | "paste";
  subject?: string | null;
}
