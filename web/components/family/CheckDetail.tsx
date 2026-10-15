"use client";

import { useEffect, useState } from "react";
import { FamilyError, familyApi } from "@/lib/family";
import type { CheckSummary, Delivery, FullResult } from "@/lib/family";
import { LABEL_WORD, fullTime, hostOf, prettySender, safeHref, timeAgo } from "@/lib/format";
import { FlagDot, VerdictMark } from "../Icons";
import { BellIcon, CheckIcon, CopyIcon, MailIcon, SendIcon } from "./icons";
import { TracePanel } from "./TracePanel";

const STATUS_WORDS: Record<string, { word: string; tone: string }> = {
  sent: { word: "Sent", tone: "green" },
  recorded: { word: "Recorded, not sent", tone: "amber" },
  skipped: { word: "Skipped", tone: "" },
  failed: { word: "Failed", tone: "red" },
  rate_limited: { word: "Held back", tone: "amber" },
};

const CHANNEL_WORDS: Record<string, string> = { email: "Email", telegram: "Telegram", all: "All channels" };
const VIA_WORDS: Record<string, string> = { telegram: "on Telegram", email: "from the email", dashboard: "from this dashboard" };

function DeliveryRow({ d }: { d: Delivery }) {
  const status = STATUS_WORDS[d.status] ?? { word: d.status, tone: "" };
  return (
    <li>
      {d.channel === "telegram" ? <SendIcon /> : d.channel === "email" ? <MailIcon /> : <BellIcon />}
      <div>
        <b>{CHANNEL_WORDS[d.channel] ?? d.channel}</b> to {d.contact}{" "}
        <span className={`status small ${status.tone ? `status--${status.tone}` : "muted"}`}>{status.word}</span>
        {d.detail && <div className="small muted">{d.detail}</div>}
      </div>
    </li>
  );
}

interface Props {
  parentId: string;
  parentFirstName: string;
  check: CheckSummary;
  onChanged: () => void;
}

export function CheckDetail({ parentId, parentFirstName, check, onChanged }: Props) {
  const [result, setResult] = useState<FullResult | null>(null);
  const [original, setOriginal] = useState<FullResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acking, setAcking] = useState(false);
  const [ackError, setAckError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    setResult(null);
    setOriginal(null);
    setError(null);
    setCopied(false);
    setAckError(null);
    familyApi
      .result(check.id)
      .then(async (r) => {
        if (!live) return;
        setResult(r);
        if (r.cached_from) {
          try {
            const o = await familyApi.result(r.cached_from);
            if (live) setOriginal(o);
          } catch {
            /* the original may be gone; the panel says so */
          }
        }
      })
      .catch((e: unknown) => live && setError(e instanceof FamilyError ? e.message : "Could not load this check."));
    return () => {
      live = false;
    };
  }, [check.id]);

  async function ack() {
    if (!check.alert) return;
    setAcking(true);
    setAckError(null);
    try {
      await familyApi.ack(parentId, check.alert.id);
      onChanged();
    } catch (e) {
      setAckError(e instanceof FamilyError ? e.message : "Could not save that. Try again.");
    } finally {
      setAcking(false);
    }
  }

  async function copyPoints(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked: the text is selectable */
    }
  }

  const label = check.label;
  const alert = check.alert;
  const sender = prettySender(check.sender);
  const flags = result?.verdict.red_flags ?? [];
  const points = (result?.verdict.family_points ?? []).map((p) => p.trim()).filter(Boolean);
  const meaning =
    label === "red" ? "Looks like a scam" : label === "yellow" ? "Could be a scam: be careful" : "Looks safe";

  return (
    <article aria-label={`${LABEL_WORD[label]} check from ${sender}`}>
      <div className={`dverdict dverdict--${label}`}>
        <div className="dverdict-top">
          <div>
            <p className={`verdict-word verdict-word--${label}`}>
              <VerdictMark kind={label} />
              {LABEL_WORD[label]}
            </p>
            <h2>{meaning}</h2>
          </div>
          <div className="score">
            <b>{check.score}</b>risk, out of 100
          </div>
        </div>
        <div className="riskbar" aria-hidden="true">
          <span style={{ width: `${Math.max(2, Math.min(100, check.score))}%` }} />
        </div>
      </div>

      <div className="dgrid">
        <div className="stack">
          <section className="card" aria-labelledby="msg-title">
            <h3 id="msg-title">The message</h3>
            <div className="meta">
              <span>
                From <b>{sender}</b>
              </span>
              <span>
                via <b>{check.channel}</b>
              </span>
              <span title={fullTime(check.created_at)}>{timeAgo(check.created_at)}</span>
              {check.demo && <span className="chip chip--nano">Sample check</span>}
            </div>
            {check.subject && (
              <p>
                <b>{check.subject}</b>
              </p>
            )}
            {result?.message.body ? (
              <div className="msgbox">{result.message.body}</div>
            ) : result ? (
              <p className="notice notice--info">
                <span>
                  Not kept. ScamShield forgets the text of safe messages (only the verdict is stored) unless you ask it to keep them.
                </span>
              </p>
            ) : null}
          </section>

          {result && points.length > 0 && (
            <section className="card" aria-labelledby="points-title">
              <h3 id="points-title">Before you call {parentFirstName}</h3>
              <ol className="points">
                {points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ol>
              <div style={{ marginTop: 14 }}>
                <button
                  type="button"
                  className="fbtn fbtn--quiet fbtn--small"
                  onClick={() => copyPoints(points.map((p) => `- ${p}`).join("\n"))}
                >
                  <CopyIcon />
                  {copied ? "Copied" : "Copy notes"}
                </button>
              </div>
            </section>
          )}

          <section className="card" aria-labelledby="alert-title">
            <h3 id="alert-title">Family alert</h3>
            {!alert ? (
              <p className="muted">
                {label === "red"
                  ? "No alert went out for this check. An alert needs a son, daughter or child among the trusted contacts."
                  : "Only a red check alerts the family. Yellow and green ones stay quiet."}
              </p>
            ) : (
              <>
                <ul className="deliveries">
                  {alert.deliveries.map((d, i) => (
                    <DeliveryRow key={`${d.channel}-${d.contact}-${i}`} d={d} />
                  ))}
                </ul>
                {alert.acked_at ? (
                  <div className="ackbox ackbox--done" role="status">
                    <CheckIcon style={{ width: 20, height: 20, verticalAlign: "-4px", marginRight: 6 }} />
                    {alert.acked_by && alert.acked_by !== "dashboard" ? alert.acked_by : "Someone"} called {parentFirstName}{" "}
                    {VIA_WORDS[alert.acked_via ?? ""] ?? ""}, {timeAgo(alert.acked_at)}.
                  </div>
                ) : (
                  <div className="ackbox">
                    <b>Nobody has said they called {parentFirstName} yet.</b>
                    <div className="small">Tap once you have spoken to her, so the family knows she is not alone with this.</div>
                    <button type="button" className="fbtn fbtn--green" onClick={ack} disabled={acking}>
                      <CheckIcon />
                      {acking ? "Saving…" : "I called her"}
                    </button>
                    {ackError && <p className="notice notice--bad formerr">{ackError}</p>}
                  </div>
                )}
              </>
            )}
          </section>

          {error && <p className="notice notice--bad">{error}</p>}

          {result && (flags.length > 0 || result.verdict.safe_action) && (
            <section className="card" aria-labelledby="why-title">
              <h3 id="why-title">Why, and what to do</h3>
              {flags.length > 0 && (
                <ul className="flags" style={{ marginBottom: 14 }}>
                  {flags.map((f, i) => {
                    const href = safeHref(f.evidence_url);
                    return (
                      <li key={i}>
                        <FlagDot />
                        <span>
                          {f.text}{" "}
                          {href && (
                            <a href={href} target="_blank" rel="noreferrer noopener">
                              Evidence: {hostOf(href)}
                            </a>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              {result.verdict.evidence_note && <p className="muted small" style={{ marginBottom: 12 }}>{result.verdict.evidence_note}</p>}
              {result.verdict.safe_action && (
                <p style={{ marginBottom: 12 }}>
                  <b>Safe next step for {parentFirstName}:</b> {result.verdict.safe_action}
                </p>
              )}
            </section>
          )}
        </div>

        <div>
          {result ? (
            <TracePanel result={result} original={original} parentFirstName={parentFirstName} />
          ) : !error ? (
            <section className="card" aria-busy="true">
              <p className="muted">Loading how this was decided…</p>
            </section>
          ) : null}
        </div>
      </div>
    </article>
  );
}
