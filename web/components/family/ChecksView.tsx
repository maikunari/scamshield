"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FamilyError, familyApi } from "@/lib/family";
import type { CheckSummary } from "@/lib/family";
import { LABEL_WORD, prettySender, timeAgo } from "@/lib/format";
import { BackIcon, VerdictMark } from "../Icons";
import { CheckDetail } from "./CheckDetail";
import { RefreshIcon } from "./icons";

const REFRESH_MS = 15_000;

function alertChip(c: CheckSummary) {
  if (!c.alert) return null;
  // Wrapped, so the row's separator (the outer span's ::before) and the status dot don't collide.
  const done = Boolean(c.alert.acked_at);
  return (
    <span>
      <span className={`status ${done ? "status--green" : "status--red"}`}>
        {done ? "Alerted · she was called" : "Alerted · not called yet"}
      </span>
    </span>
  );
}

interface Props {
  parentId: string;
  parentFirstName: string;
  initialCheck: string | null;
  onUnauthorized: () => void;
  onPendingCount: (n: number) => void;
}

export function ChecksView({ parentId, parentFirstName, initialCheck, onUnauthorized, onPendingCount }: Props) {
  const [checks, setChecks] = useState<CheckSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(initialCheck);
  const [view, setView] = useState<"list" | "detail">(initialCheck ? "detail" : "list");
  const [refreshing, setRefreshing] = useState(false);
  const picked = useRef(Boolean(initialCheck));

  const load = useCallback(
    async (manual = false) => {
      if (manual) setRefreshing(true);
      try {
        const rows = await familyApi.checks(parentId);
        setChecks(rows);
        setError(null);
        onPendingCount(rows.filter((c) => c.alert && !c.alert.acked_at).length);
        // The newest check is selected straight away (a wide screen shows it beside the list; a phone shows the list first).
        if (!picked.current && rows[0]) {
          picked.current = true;
          setSelected(rows[0].id);
        }
      } catch (e) {
        if (e instanceof FamilyError && e.status === 401) return onUnauthorized();
        setError(e instanceof FamilyError ? e.message : "Could not load the checks.");
      } finally {
        if (manual) setRefreshing(false);
      }
    },
    [parentId, onUnauthorized, onPendingCount],
  );

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const current = checks?.find((c) => c.id === selected) ?? null;

  function pick(id: string) {
    picked.current = true;
    setSelected(id);
    setView("detail");
    window.scrollTo({ top: 0 });
  }

  return (
    <div className="checks" data-view={view}>
      <section className="checks-list" aria-label="Recent checks">
        <div className="list-head">
          <h2>Recent checks</h2>
          <button type="button" className="fbtn fbtn--quiet fbtn--small" onClick={() => load(true)} disabled={refreshing}>
            <RefreshIcon />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        {error && <p className="notice notice--bad">{error}</p>}
        {checks === null && !error && <p className="muted">Loading…</p>}
        {checks !== null && checks.length === 0 && (
          <div className="card empty">
            <h3>Nothing checked yet</h3>
            <p>
              When {parentFirstName} checks a message it shows up here, with the alert and how it was decided. You can try one yourself on the{" "}
              <a href="/check/">check page</a>.
            </p>
          </div>
        )}
        {checks && checks.length > 0 && (
          <ul className="citems">
            {checks.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className="citem"
                  aria-current={c.id === selected}
                  onClick={() => pick(c.id)}
                >
                  <span className="citem-top">
                    <span className={`verdict-word verdict-word--${c.label}`}>
                      <VerdictMark kind={c.label} />
                      {LABEL_WORD[c.label]}
                    </span>
                    <span className="citem-score">{c.score}/100</span>
                    <span className="when">{timeAgo(c.created_at)}</span>
                  </span>
                  <span className="citem-sender">
                    {prettySender(c.sender)}
                    <span className="citem-channel"> · {c.channel}</span>
                  </span>
                  <span className="citem-snippet">
                    {c.snippet || c.summary || "Safe message: the text was not kept."}
                  </span>
                  <span className="citem-foot">
                    {alertChip(c)}
                    {c.demo && <span>Sample</span>}
                    {c.trace.model_calls > 0 && <span>{c.trace.model_calls} Nemotron call{c.trace.model_calls === 1 ? "" : "s"}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="checks-detail" aria-live="polite">
        <button type="button" className="fbtn fbtn--quiet fbtn--small back-to-list" onClick={() => setView("list")}>
          <BackIcon />
          All checks
        </button>
        {current ? (
          <CheckDetail
            parentId={parentId}
            parentFirstName={parentFirstName}
            check={current}
            onChanged={() => void load()}
          />
        ) : (
          checks !== null &&
          checks.length > 0 && (
            <div className="card empty">
              <h3>{selected ? "That check is no longer here" : "Pick a check"}</h3>
              <p>Choose one on the left to see the message, the alert and how ScamShield decided.</p>
            </div>
          )
        )}
      </section>
    </div>
  );
}
