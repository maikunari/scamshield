"use client";

import { useState } from "react";
import { CheckError, deleteMyData } from "@/lib/api";
import { BackIcon, ShieldIcon } from "./Icons";

type Phase = "ask" | "busy" | "done";

/** "Delete my data": one clear question first, then the deletion. Nothing is removed until the red button. */
export function DeleteData() {
  const [phase, setPhase] = useState<Phase>("ask");
  const [error, setError] = useState<string | null>(null);

  async function confirmDelete() {
    setPhase("busy");
    setError(null);
    try {
      await deleteMyData();
      setPhase("done");
    } catch (err) {
      console.error("delete failed", err);
      setError(err instanceof CheckError && err.status ? "It could not finish deleting." : "It could not be reached.");
      setPhase("ask");
    }
  }

  return (
    <main className="shell">
      <header className="brand">
        <ShieldIcon />
        <span>ScamShield</span>
      </header>

      {phase === "done" ? (
        <>
          <section className="hero">
            <h1>Your data is deleted.</h1>
            <p>Your messages, ScamShield&apos;s answers, the notes it sent your family and what it remembered about you are gone.</p>
          </section>
          <a className="btn btn-primary" href="/check/">
            Back to ScamShield
          </a>
        </>
      ) : (
        <>
          <section className="hero">
            <h1>Delete my data?</h1>
            <p>This removes every message you checked, every answer, the notes sent to your family, and what ScamShield remembers about you.</p>
            <p className="plain-warning">It cannot be undone.</p>
          </section>

          {error && (
            <div className="error" role="alert">
              <strong>Nothing was lost.</strong>
              {error} Please try again in a moment.
            </div>
          )}

          <div className="stack">
            <button type="button" className="btn btn-danger" disabled={phase === "busy"} onClick={() => void confirmDelete()}>
              {phase === "busy" ? "Deleting" : "Yes, delete everything"}
            </button>
            <a className="btn btn-secondary btn-center" href="/check/" aria-disabled={phase === "busy"}>
              <BackIcon />
              No, keep it
            </a>
          </div>

          <p className="privacy-note">
            Want a copy first? <a href="/api/my-data/export" download="scamshield-my-data.json">Download my data</a>
          </p>
        </>
      )}
    </main>
  );
}
