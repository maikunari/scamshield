"use client";

import { useState } from "react";
import { FamilyError, familyApi } from "@/lib/family";
import type { Gate, Session } from "@/lib/family";
import { LockIcon, ShieldIcon } from "../Icons";
import { DemoLoader } from "./DemoLoader";

interface Props {
  gate: Gate | null;
  onSignedIn: (session: Session) => void;
}

export function LoginGate({ gate, onSignedIn }: Props) {
  const [passphrase, setPassphrase] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn(phrase: string) {
    setBusy(true);
    setError(null);
    try {
      const s = await familyApi.login(phrase);
      onSignedIn({ parent_id: s.parent_id, name: s.name });
    } catch (e) {
      setError(
        e instanceof FamilyError && e.status === 429
          ? "Too many tries. Wait a minute and try again."
          : e instanceof FamilyError
            ? e.message
            : "Could not sign in.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <div className="brand">
        <ShieldIcon />
        ScamShield
      </div>
      <h1>Your family’s dashboard</h1>
      <p className="lede">See what was checked, who was alerted, and how ScamShield decided. Only your family has the passphrase.</p>

      <form
        className="stack"
        style={{ marginTop: 24 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (passphrase.trim()) void signIn(passphrase);
        }}
      >
        <div className="ffield">
          <label htmlFor="passphrase">Family passphrase</label>
          <div className="pwfield">
            <input
              id="passphrase"
              className="finput"
              type={show ? "text" : "password"}
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              autoComplete="current-password"
              autoCapitalize="none"
              spellCheck={false}
              aria-describedby={error ? "login-error" : undefined}
            />
            <button type="button" onClick={() => setShow((v) => !v)} aria-pressed={show}>
              {show ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        {error && (
          <p id="login-error" className="notice notice--bad" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="fbtn fbtn--primary fbtn--block" disabled={busy || !passphrase.trim()}>
          <LockIcon />
          {busy ? "Opening…" : "Open the dashboard"}
        </button>
      </form>

      {gate?.demo_load && (
        <section className="judge" aria-labelledby="judge-title">
          <h2 id="judge-title">Judging this?</h2>
          {gate.demo_passphrase && (
            <div className="stack" style={{ gap: 10, marginBottom: 14 }}>
              <p className="small muted">
                The demo family’s passphrase is <span className="passphrase-box">{gate.demo_passphrase}</span>
              </p>
              <button type="button" className="fbtn fbtn--green fbtn--block" onClick={() => void signIn(gate.demo_passphrase ?? "")} disabled={busy}>
                Open as the demo family
              </button>
            </div>
          )}
          <DemoLoader
            compact
            demoPassphrase={gate.demo_passphrase}
          />
        </section>
      )}
    </main>
  );
}
