"use client";

import { useCallback, useEffect, useState } from "react";
import { FamilyError, familyApi } from "@/lib/family";
import type { Gate, Session } from "@/lib/family";
import { ShieldIcon } from "../Icons";
import { AlertsPanel } from "./AlertsPanel";
import { ChecksView } from "./ChecksView";
import { LoginGate } from "./LoginGate";
import { MemoryPanel } from "./MemoryPanel";
import { BellIcon, BookIcon, ListIcon } from "./icons";
import "./family.css";

type Tab = "checks" | "memory" | "alerts";

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "checks", label: "Checks", icon: <ListIcon /> },
  { id: "memory", label: "Memory", icon: <BookIcon /> },
  { id: "alerts", label: "Alerts", icon: <BellIcon /> },
];

function firstName(full: string): string {
  return full.trim().split(/\s+/)[0] || "your parent";
}

export function FamilyApp() {
  const [session, setSession] = useState<Session | null | undefined>(undefined); // undefined: still asking
  const [gate, setGate] = useState<Gate | null>(null);
  const [tab, setTab] = useState<Tab>("checks");
  const [pending, setPending] = useState(0);
  const [deepLink, setDeepLink] = useState<string | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setDeepLink(q.get("check"));
    familyApi
      .session()
      .then(setSession)
      .catch((e: unknown) => {
        if (!(e instanceof FamilyError) || e.status !== 401) console.error("session check failed", e);
        setSession(null);
      });
    familyApi.gate().then(setGate).catch(() => setGate(null));
  }, []);

  const signedOut = useCallback(() => setSession(null), []);

  async function signOut() {
    try {
      await familyApi.logout();
    } finally {
      setSession(null);
      setPending(0);
    }
  }

  if (session === undefined) {
    return (
      <div className="fam">
        <p className="muted" style={{ textAlign: "center", marginTop: "20vh" }}>
          Opening…
        </p>
      </div>
    );
  }

  if (session === null) {
    return (
      <div className="fam">
        <LoginGate gate={gate} onSignedIn={setSession} />
      </div>
    );
  }

  const name = firstName(session.name);
  return (
    <div className="fam">
      <div className="fam-wrap">
        <header className="fhead">
          <div className="brand">
            <ShieldIcon />
            ScamShield
          </div>
          <span className="fhead-who">
            Watching over <strong>{session.name}</strong>
          </span>
          <button type="button" className="fbtn fbtn--quiet fbtn--small" onClick={signOut}>
            Sign out
          </button>
        </header>

        <div className="ftabs" role="tablist" aria-label="Dashboard sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              className="ftab"
              onClick={() => setTab(t.id)}
            >
              {t.icon}
              {t.label}
              {t.id === "checks" && pending > 0 && (
                <span className="count" aria-label={`${pending} alerts waiting for a call`}>
                  {pending}
                </span>
              )}
            </button>
          ))}
        </div>

        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "checks" && (
            <ChecksView
              parentId={session.parent_id}
              parentFirstName={name}
              initialCheck={deepLink}
              onUnauthorized={signedOut}
              onPendingCount={setPending}
            />
          )}
          {tab === "memory" && <MemoryPanel parentId={session.parent_id} parentFirstName={name} onUnauthorized={signedOut} />}
          {tab === "alerts" && <AlertsPanel parentId={session.parent_id} parentFirstName={name} onUnauthorized={signedOut} />}
        </div>
      </div>
    </div>
  );
}
