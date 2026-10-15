"use client";

import { useEffect, useRef, useState } from "react";
import type { CheckResponse, Label } from "@/lib/types";
import { copyFor } from "@/lib/verdictCopy";
import { speak, stopSpeaking, watchVoices } from "@/lib/speech";
import { BackIcon, ExternalIcon, FlagDot, ShieldIcon, SpeakerIcon, StopIcon, VerdictMark } from "./Icons";

const MAX_FLAGS = 3;

const THEME: Record<Label, string> = { red: "#9E1B1B", yellow: "#F0B429", green: "#19603E" };

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

interface Props {
  result: CheckResponse;
  onBack: () => void;
}

export function VerdictScreen({ result, onBack }: Props) {
  const { title, lede, sub } = copyFor(result);
  const flags = result.red_flags.slice(0, MAX_FLAGS);
  const showWhy = result.label !== "green" && flags.length > 0;
  const [speaking, setSpeaking] = useState(false);
  const [canSpeak, setCanSpeak] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  // No voices installed (or not loaded yet): no "Read this to me" button rather than one that does nothing.
  useEffect(() => watchVoices(setCanSpeak), []);

  // Focus the verdict for screen readers, tint the browser chrome, and stop any speech on leave.
  useEffect(() => {
    headingRef.current?.focus();
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    const previous = meta?.content;
    if (meta) meta.content = THEME[result.label];
    window.scrollTo({ top: 0 });
    return () => {
      stopSpeaking();
      if (meta && previous) meta.content = previous;
    };
  }, [result.label]);

  const spokenText = [
    `${title}.`,
    lede,
    sub,
    showWhy ? `Why: ${flags.map((f) => f.text).join(" ")}` : "",
    result.label !== "green" ? `What to do now: ${result.safe_action}` : "",
  ]
    .filter(Boolean)
    .join(" ");

  async function toggleSpeech() {
    if (speaking) {
      stopSpeaking();
      setSpeaking(false);
      return;
    }
    setSpeaking(true);
    await speak(spokenText, result.language === "ja" ? "ja-JP" : "en-US");
    setSpeaking(false);
  }

  return (
    <main className={`verdict verdict--${result.label}`} aria-live="assertive">
      <div className="verdict-inner">
        <p className="verdict-kicker">
          <ShieldIcon />
          ScamShield checked this message
        </p>

        <header className="verdict-head">
          <VerdictMark kind={result.label} className="verdict-mark" />
          <h1 ref={headingRef} tabIndex={-1}>
            {title}
          </h1>
          <p className="verdict-lede">{lede}</p>
          <p className="verdict-sub">{sub}</p>
        </header>

        {showWhy && (
          <section className="panel" aria-labelledby="why-head">
            <h2 id="why-head">Why</h2>
            <ul className="flags">
              {flags.map((f, i) => (
                <li key={i} className="flag">
                  <FlagDot />
                  <span>{f.text}</span>
                  {f.evidence_url && (
                    <a href={f.evidence_url} target="_blank" rel="noopener noreferrer nofollow">
                      See the report on {hostOf(f.evidence_url)}
                      <ExternalIcon />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {result.label !== "green" && (
          <section className="panel" aria-labelledby="do-head">
            <h2 id="do-head">What to do now</h2>
            <p>{result.safe_action}</p>
          </section>
        )}

        <div className="verdict-actions">
          {canSpeak && (
            <button type="button" className="btn btn-speak" onClick={toggleSpeech} aria-pressed={speaking}>
              {speaking ? <StopIcon /> : <SpeakerIcon />}
              {speaking ? "Stop reading" : "Read this to me"}
            </button>
          )}
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            <BackIcon />
            Check another message
          </button>
          <p className="verdict-foot">
            {result.evidence_note}
            {result.offline ? " Demo mode: offline checker." : ""}
          </p>
        </div>
      </div>
    </main>
  );
}
