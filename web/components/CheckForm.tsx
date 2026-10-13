"use client";

import { useId } from "react";
import { EXAMPLES, type Example } from "@/lib/examples";
import { LockIcon, ShieldIcon } from "./Icons";

interface Props {
  body: string;
  sender: string;
  busy: boolean;
  error: string | null;
  onBodyChange: (v: string) => void;
  onSenderChange: (v: string) => void;
  onSubmit: () => void;
  onExample: (ex: Example) => void;
}

export function CheckForm({ body, sender, busy, error, onBodyChange, onSenderChange, onSubmit, onExample }: Props) {
  const bodyId = useId();
  const senderId = useId();
  const canSubmit = body.trim().length > 0 && !busy;

  return (
    <main className="shell">
      <header className="brand">
        <ShieldIcon />
        <span>ScamShield</span>
      </header>

      <section className="hero">
        <h1>Is this message a scam?</h1>
        <p>Paste it below. You will get a clear answer in a few seconds.</p>
      </section>

      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) onSubmit();
        }}
      >
        <div className="field">
          <label htmlFor={bodyId}>The message</label>
          <textarea
            id={bodyId}
            className="textarea"
            value={body}
            onChange={(e) => onBodyChange(e.target.value)}
            placeholder="Paste the text message or email here"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={busy}
            required
          />
        </div>

        <div className="field">
          <label htmlFor={senderId}>
            Who sent it? <span className="hint">(phone number or email, if you know)</span>
          </label>
          <input
            id={senderId}
            className="input"
            type="text"
            inputMode="text"
            value={sender}
            onChange={(e) => onSenderChange(e.target.value)}
            placeholder="For example 212-555-0101"
            autoComplete="off"
            disabled={busy}
          />
        </div>

        {error && (
          <div className="error" role="alert">
            <strong>ScamShield could not check this right now.</strong>
            {error} Please try again in a moment. Until someone in your family has looked at it, do not reply, click or pay.
          </div>
        )}

        {busy ? (
          <div className="checking" role="status" aria-live="polite">
            <span className="dots" aria-hidden>
              <i /><i /><i />
            </span>
            Checking the message
          </div>
        ) : (
          <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
            Check this message
          </button>
        )}
      </form>

      <section className="examples" aria-labelledby="examples-head">
        <h2 id="examples-head" className="examples-head">
          or try an example
        </h2>
        {EXAMPLES.map((ex) => (
          <button key={ex.id} type="button" className="btn btn-secondary" disabled={busy} onClick={() => onExample(ex)}>
            <span>
              <span className="ex-title">{ex.title}</span>
              <span className="ex-sub">From {ex.sender}</span>
            </span>
          </button>
        ))}
      </section>

      <p className="privacy">
        <LockIcon />
        <span>Only ScamShield sees what you paste. Card numbers are hidden before checking, and links in the message are never opened.</span>
      </p>

      <nav className="footer-links" aria-label="Privacy">
        <a href="/privacy/">How your privacy is protected</a>
        <a href="/delete/">Delete my data</a>
      </nav>
    </main>
  );
}
