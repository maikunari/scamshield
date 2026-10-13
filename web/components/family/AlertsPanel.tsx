"use client";

import { useCallback, useEffect, useState } from "react";
import { FamilyError, familyApi } from "@/lib/family";
import type { FamilyInfo, TelegramLink } from "@/lib/family";
import { safeHref } from "@/lib/format";
import { CheckIcon, ClockIcon, ExternalIcon, MailIcon, SendIcon } from "./icons";
import { Qr } from "./Qr";

const POLL_MS = 3000;

interface Props {
  parentId: string;
  parentFirstName: string;
  onUnauthorized: () => void;
}

export function AlertsPanel({ parentId, parentFirstName, onUnauthorized }: Props) {
  const [info, setInfo] = useState<FamilyInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [links, setLinks] = useState<Record<string, TelegramLink>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setInfo(await familyApi.family(parentId));
      setError(null);
    } catch (e) {
      if (e instanceof FamilyError && e.status === 401) return onUnauthorized();
      setError(e instanceof FamilyError ? e.message : "Could not load the family list.");
    }
  }, [parentId, onUnauthorized]);

  useEffect(() => {
    void load();
  }, [load]);

  // While a link is waiting for the family member to tap Start, look for the chat to appear.
  useEffect(() => {
    const names = Object.keys(links);
    if (names.length === 0) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [links, load]);

  async function makeLink(contact: string) {
    setBusy(contact);
    setLinkError(null);
    try {
      const link = await familyApi.telegramLink(parentId, contact);
      setLinks((cur) => ({ ...cur, [contact]: link }));
    } catch (e) {
      if (e instanceof FamilyError && e.status === 401) return onUnauthorized();
      setLinkError(e instanceof FamilyError ? e.message : "Could not make a link. Try again.");
    } finally {
      setBusy(null);
    }
  }

  if (error) return <p className="notice notice--bad">{error}</p>;
  if (!info) return <p className="muted">Loading…</p>;

  return (
    <div>
      <p className="muted" style={{ marginBottom: 16, maxWidth: "46em" }}>
        When a message looks like a scam, ScamShield tells the people below straight away, with the evidence and calm words for the phone call. Everything it
        sends is also kept on the Checks tab.
      </p>
      {linkError && <p className="notice notice--bad" style={{ marginBottom: 16 }}>{linkError}</p>}
      {info.contacts.length === 0 ? (
        <section className="card empty">
          <h3>No one to alert yet</h3>
          <p>Alerts go to a son, daughter or child in {parentFirstName}’s trusted contacts. Add one on the Memory tab.</p>
        </section>
      ) : (
        <div className="alert-grid">
          {info.contacts.map((c) => {
            const link = links[c.name];
            const url = link?.url ? safeHref(link.url) : null;
            return (
              <section key={c.name} className="card" aria-labelledby={`fc-${c.name}`}>
                <h3 id={`fc-${c.name}`}>
                  {c.name} <span className="chip">{c.relationship}</span>
                </h3>

                <div className="channel">
                  <MailIcon />
                  <div>
                    <b>Email</b>
                    {c.emails.length === 0 ? (
                      <p className="muted">No email address yet. Add one on the Memory tab.</p>
                    ) : (
                      <>
                        <p>{c.emails.join(", ")}</p>
                        <p className="small muted">
                          {info.email_configured
                            ? "Alerts are emailed, with an “I called her” link."
                            : "Email isn’t connected on this deployment, so alerts are recorded here instead of sent."}
                        </p>
                      </>
                    )}
                  </div>
                </div>

                <div className="channel">
                  <SendIcon />
                  <div>
                    <b>Telegram</b>
                    {c.telegram_linked ? (
                      <p>
                        <span className="chip chip--green">
                          <CheckIcon /> Linked
                        </span>{" "}
                        <span className="small muted">Alerts arrive in {c.name.split(" ")[0]}’s Telegram with an “I called her” button.</span>
                      </p>
                    ) : (
                      <>
                        <p className="small muted">
                          {info.telegram_configured
                            ? "Not linked yet. Open the link on the phone that has Telegram and tap Start."
                            : "This deployment has no Telegram bot yet, so there is nothing to link. Email and this dashboard still work."}
                        </p>
                        {!link && (
                          <div style={{ marginTop: 10 }}>
                            <button type="button" className="fbtn fbtn--primary fbtn--small" onClick={() => makeLink(c.name)} disabled={busy === c.name}>
                              <SendIcon />
                              {busy === c.name ? "Making a link…" : "Link Telegram"}
                            </button>
                          </div>
                        )}
                        {link && (
                          <div className="linkpanel" role="status">
                            {link.url ? <Qr value={link.url} label={`QR code that links ${c.name}’s Telegram`} /> : null}
                            <div className="stack" style={{ gap: 10 }}>
                              {url ? (
                                <>
                                  <p>
                                    <b>Scan this on {c.name.split(" ")[0]}’s phone,</b> or open the link there.
                                  </p>
                                  <a className="fbtn fbtn--primary fbtn--small" href={url} target="_blank" rel="noreferrer noopener">
                                    <ExternalIcon />
                                    Open in Telegram
                                  </a>
                                  <p className="small linkurl mono">{url}</p>
                                </>
                              ) : (
                                <p>
                                  The link code is <span className="passphrase-box mono">{link.code}</span>. It becomes a link once the bot’s name is
                                  configured.
                                </p>
                              )}
                              <p className="small muted">
                                <ClockIcon style={{ width: 16, height: 16, verticalAlign: "-3px" }} /> One use, good for 7 days. This page notices when the chat is
                                linked.
                              </p>
                            </div>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
