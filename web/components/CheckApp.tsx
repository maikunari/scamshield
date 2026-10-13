"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckError, checkMessage } from "@/lib/api";
import type { Example } from "@/lib/examples";
import type { CheckResponse } from "@/lib/types";
import { CheckForm } from "./CheckForm";
import { VerdictScreen } from "./VerdictScreen";

type Phase = { kind: "form" } | { kind: "result"; result: CheckResponse };

/** Text arriving via the Android Web Share Target (`/check/?title=&text=&url=`). */
function sharedMessage(): string | null {
  if (typeof window === "undefined") return null;
  const q = new URLSearchParams(window.location.search);
  const parts = [q.get("title"), q.get("text"), q.get("url")].map((s) => (s ?? "").trim()).filter(Boolean);
  if (parts.length === 0) return null;
  // Some apps put the URL inside `text` as well; drop a duplicate.
  const text = parts.filter((p, i) => parts.indexOf(p) === i).join("\n");
  return text || null;
}

export function CheckApp() {
  const [body, setBody] = useState("");
  const [sender, setSender] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "form" });
  const inflight = useRef<AbortController | null>(null);

  const run = useCallback(async (text: string, from: string) => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result = await checkMessage({ body: text, sender: from.trim() || null, channel: "paste" }, controller.signal);
      if (!controller.signal.aborted) setPhase({ kind: "result", result });
    } catch (err) {
      if (controller.signal.aborted) return;
      console.error("check failed", err);
      // Plain words for the parent; the real error goes to the console.
      setError(err instanceof CheckError && err.status ? "It could not finish checking." : "It could not be reached.");
    } finally {
      if (inflight.current === controller) {
        inflight.current = null;
        setBusy(false);
      }
    }
  }, []);

  // Shared from another app: prefill and check straight away, then clean the URL so a reload
  // does not re-run the check.
  useEffect(() => {
    const shared = sharedMessage();
    if (!shared) return;
    setBody(shared);
    window.history.replaceState(null, "", window.location.pathname);
    void run(shared, "");
  }, [run]);

  useEffect(() => () => inflight.current?.abort(), []);

  function onExample(ex: Example) {
    setBody(ex.body);
    setSender(ex.sender);
    void run(ex.body, ex.sender);
  }

  function back() {
    setPhase({ kind: "form" });
    setBody("");
    setSender("");
    setError(null);
  }

  if (phase.kind === "result") {
    return <VerdictScreen result={phase.result} onBack={back} />;
  }

  return (
    <CheckForm
      body={body}
      sender={sender}
      busy={busy}
      error={error}
      onBodyChange={setBody}
      onSenderChange={setSender}
      onSubmit={() => void run(body, sender)}
      onExample={onExample}
    />
  );
}
