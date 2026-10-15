"use client";

import { useEffect, useRef } from "react";
import type { Progress } from "@/lib/progress";
import { TickIcon, VerdictMark } from "./Icons";

const QUICK_TITLE = { red: "This looks like a scam", yellow: "Be careful", green: "Looks safe so far" } as const;

/** The check as it happens: each step the server reports, plus an early answer when the case is obvious. */
export function CheckProgress({ progress }: { progress: Progress }) {
  const ref = useRef<HTMLDivElement>(null);

  // An example is tapped below the form: bring the progress into view.
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    ref.current?.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
  }, []);

  const { quick } = progress;
  return (
    <div ref={ref} className="progress" role="status" aria-live="polite">
      {quick && (
        <div className={`quick quick--${quick.label}`}>
          <VerdictMark kind={quick.label} className="quick-mark" />
          <div>
            <strong>{QUICK_TITLE[quick.label]}</strong>
            <p>{quick.reason}</p>
            <p className="quick-sub">
              {quick.label === "red" ? "Do not reply, click or pay. " : ""}Finishing the check…
            </p>
          </div>
        </div>
      )}
      <ol className="steps">
        {progress.lines.map((line) => (
          <li key={line.key} className={line.done ? "step step--done" : "step"}>
            {line.done ? (
              <TickIcon className="step-icon" />
            ) : (
              <span className="dots step-icon" aria-hidden>
                <i />
                <i />
                <i />
              </span>
            )}
            <span>{line.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
