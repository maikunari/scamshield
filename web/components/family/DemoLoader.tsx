"use client";

import { useState } from "react";
import { FamilyError, familyApi } from "@/lib/family";
import type { DemoLoaded } from "@/lib/family";
import { CheckIcon } from "./icons";

interface Props {
  /** The public demo passphrase, when this deployment still uses it. */
  demoPassphrase: string | null;
  compact?: boolean;
}

/** "Load demo family": resets the demo family to the seed (a parent, two trusted contacts, past checks, one red alert). */
export function DemoLoader({ demoPassphrase, compact }: Props) {
  const [loaded, setLoaded] = useState<DemoLoaded | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setLoaded(await familyApi.loadDemo());
    } catch (e) {
      setError(
        e instanceof FamilyError && e.status === 429
          ? "Too many tries. Wait a minute and try again."
          : e instanceof FamilyError
            ? e.message
            : "Could not load the demo family.",
      );
    } finally {
      setBusy(false);
    }
  }

  const phrase = loaded?.passphrase ?? demoPassphrase;

  return (
    <div className="stack" style={{ gap: 12 }}>
      {!loaded ? (
        <>
          <p className={compact ? "small muted" : "muted"}>
            Fills in a demo family: Michiko, her son Kenji and grandson Taro as trusted contacts, and a few past checks including a scam that alerted Kenji. No
            typing needed.
          </p>
          <button type="button" className="fbtn fbtn--primary fbtn--block" onClick={load} disabled={busy}>
            {busy ? "Loading…" : "Load demo family"}
          </button>
        </>
      ) : (
        <>
          <ol className="steps">
            <li className="done">
              <span className="n">
                <CheckIcon />
              </span>
              <span>
                <b>Demo family loaded.</b> {loaded.name}, {loaded.checks} past checks, {loaded.alerts} alerts.
              </span>
            </li>
            <li>
              <span className="n">2</span>
              <span>
                <a href="/check/">Try an example on the check page</a>. Tap one of the three buttons and watch it turn red.
              </span>
            </li>
            <li>
              <span className="n">3</span>
              <span>
                Open the <a href="/family/">family dashboard</a>
                {phrase ? (
                  <>
                    {" "}
                    with the passphrase <span className="passphrase-box">{phrase}</span> to see the alert and the trace.
                  </>
                ) : (
                  <> with your configured passphrase.</>
                )}
              </span>
            </li>
          </ol>
        </>
      )}
      {error && (
        <p className="notice notice--bad" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
