import type { FullResult, TraceStep } from "@/lib/family";
import { fmtNumber, fmtSeconds, fmtUsd, hostOf, modelFamily, safeHref, stepCopy, stepQuery } from "@/lib/format";
import { ChipIcon } from "./icons";

const SIGNAL_WORDS: Record<string, string> = {
  urgency: "Urgency",
  money_request: "Asks for money",
  secrecy_request: "Asks for secrecy",
  credential_request: "Asks to log in or call back",
  link_present: "Has a link",
  phone_present: "Has a phone number",
};

const SKILL_WORDS: Record<string, string> = { check_phone: "Phone number", check_url: "Link", check_phrase: "Wording" };

function totals(steps: TraceStep[]) {
  const models = steps.filter((s) => s.kind === "model" && s.model && !s.error);
  return {
    calls: models.length,
    tokensIn: steps.reduce((n, s) => n + s.tokens_in, 0),
    tokensOut: steps.reduce((n, s) => n + s.tokens_out, 0),
    ms: steps.reduce((n, s) => n + s.latency_ms, 0),
    usd: steps.reduce((n, s) => n + s.cost_usd, 0),
    families: Array.from(new Map(models.map((s) => [s.model as string, modelFamily(s)])).entries()),
  };
}

function Badge({ step }: { step: TraceStep }) {
  if (step.kind === "model") {
    const f = modelFamily(step);
    return <span className={`chip chip--${(f ?? "nano").toLowerCase()}`}>{f ? `Nemotron ${f}` : "Model"}</span>;
  }
  if (step.kind === "tool") return <span className="chip chip--tool">Tavily</span>;
  return <span className="chip">On this server</span>;
}

function Step({ step, index, maxMs, parent }: { step: TraceStep; index: number; maxMs: number; parent: string }) {
  const { title, blurb } = stepCopy(step, parent);
  const query = step.kind === "tool" ? stepQuery(step) : null;
  const detail = query ? step.detail.slice(0, step.detail.indexOf("| q=")).trim() : step.detail;
  const pct = maxMs > 0 ? Math.max(2, Math.round((step.latency_ms / maxMs) * 100)) : 2;
  const tokens = step.tokens_in + step.tokens_out;
  return (
    <li className={`tstep tstep--${step.kind}${step.error ? " tstep--err" : ""}`}>
      <span className="tnum" aria-hidden="true">
        {index + 1}
      </span>
      <div>
        <div className="thead">
          <h4>{title}</h4>
          <Badge step={step} />
        </div>
        {blurb && step.kind !== "local" && <p className="tblurb">{blurb}</p>}
        {step.kind === "model" && step.model && <p className="tmodel mono">{step.model}</p>}
        {query && (
          <p className="tdetail">
            <span className="muted">Searched for </span>
            <span className="mono">{query}</span>
          </p>
        )}
        {detail && (step.kind === "local" || detail !== "") && <p className="tdetail">{detail}</p>}
        {step.error && <p className="terror">Did not complete: {step.error}</p>}
        {step.kind !== "local" && (
          <div className="meter" aria-hidden="true">
            <span style={{ width: `${pct}%` }} />
          </div>
        )}
        <div className="tstats">
          <span>
            <b>{fmtSeconds(step.latency_ms)}</b>
          </span>
          {step.kind === "model" && (
            <span>
              <b>{fmtNumber(step.tokens_in)}</b> in · <b>{fmtNumber(step.tokens_out)}</b> out
              {tokens === 0 ? " (no call made)" : ""}
            </span>
          )}
          {step.cost_usd > 0 && (
            <span>
              <b>{fmtUsd(step.cost_usd)}</b>
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

interface Props {
  result: FullResult;
  /** The run a cached verdict was replayed from, when it could be loaded. */
  original: FullResult | null;
  parentFirstName: string;
}

export function TracePanel({ result, original, parentFirstName }: Props) {
  const source = original ?? result;
  const steps = source.trace.steps;
  const t = totals(steps);
  const maxMs = Math.max(0, ...steps.filter((s) => s.kind !== "local").map((s) => s.latency_ms));
  const impersonation = result.triage.signals.impersonation;
  const signalChips = [
    ...Object.entries(SIGNAL_WORDS).filter(([k]) => result.triage.signals[k] === true).map(([, word]) => word),
    ...(typeof impersonation === "string" && impersonation !== "none" ? [`Poses as: ${impersonation.replace("_", " ")}`] : []),
  ];
  const lookups = result.lookups;

  return (
    <section className="card trace" aria-labelledby="trace-title">
      <div className="trace-head">
        <h3 id="trace-title">
          <ChipIcon />
          How ScamShield decided
        </h3>
        <p className="muted small">
          {t.families.length >= 2
            ? "Small models do the quick work, the largest does the judging. Every step is logged with its model, tokens, time and cost."
            : "Every step is logged with the model that ran it, its tokens, time and cost."}
        </p>
        <div className="tiles">
          <div className="tile">
            <b>{t.calls}</b>
            <span>Nemotron call{t.calls === 1 ? "" : "s"}</span>
          </div>
          <div className="tile">
            <b>{fmtNumber(t.tokensIn + t.tokensOut)}</b>
            <span>
              tokens ({fmtNumber(t.tokensIn)} in, {fmtNumber(t.tokensOut)} out)
            </span>
          </div>
          <div className="tile">
            <b>{fmtSeconds(t.ms)}</b>
            <span>start to finish</span>
          </div>
          <div className="tile">
            <b>{fmtUsd(t.usd)}</b>
            <span>estimated cost</span>
          </div>
        </div>
        {t.families.length > 0 && (
          <div className="models-used" aria-label="Models used">
            {t.families.map(([id, family]) => (
              <span key={id} className={`chip chip--${(family ?? "nano").toLowerCase()} mono`} title={id}>
                {id}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="trace-body">
        {result.demo && (
          <p className="notice notice--info" style={{ marginTop: 12 }}>
            <span>
              <b>Sample check.</b> This one ships with the demo family, so its model ids are real but the token counts and timings are
              illustrative. Run an example on the check page to see a measured trace.
            </span>
          </p>
        )}
        {original && (
          <p className="notice notice--info" style={{ marginTop: 12 }}>
            <span>
              <b>Replayed verdict.</b> The same message was checked before with the same memory, so nothing was called again. This is the
              original run it was served from.
            </span>
          </p>
        )}
        {result.cached_from && !original && (
          <p className="notice notice--info" style={{ marginTop: 12 }}>
            <span>
              <b>Replayed verdict.</b> Served from an earlier check, so no model or search was called this time.
            </span>
          </p>
        )}
        {result.offline && !result.demo && (
          <p className="notice" style={{ marginTop: 12 }}>
            <span>
              <b>Offline fakes.</b> This deployment ran without live keys, so a stand-in answered instead of Nemotron.
            </span>
          </p>
        )}

        <h4 className="trace-sub">Step by step</h4>
        <ol className="tsteps">
          {steps.map((s, i) => (
            <Step key={`${s.name}-${i}`} step={s} index={i} maxMs={maxMs} parent={parentFirstName} />
          ))}
        </ol>

        <h4 className="trace-sub">What fed the verdict</h4>
        <div className="stack" style={{ gap: 12 }}>
          <div>
            <p className="small muted" style={{ marginBottom: 6 }}>
              Signals found in the message
            </p>
            <div className="signals">
              {signalChips.map((word) => (
                <span key={word} className="chip chip--amber">
                  {word}
                </span>
              ))}
              {signalChips.length === 0 && <span className="chip chip--green">None found</span>}
            </div>
          </div>
          <div>
            <p className="small muted" style={{ marginBottom: 6 }}>
              Sender against {parentFirstName}’s memory
            </p>
            <p>
              <span className={`chip ${result.contact.status === "unknown" ? "chip--amber" : "chip--green"}`}>
                {result.contact.status === "trusted"
                  ? "Trusted contact"
                  : result.contact.status === "known_service"
                    ? "Known service"
                    : "Not in her memory"}
              </span>{" "}
              {result.contact.reason}
            </p>
          </div>
          <div>
            <p className="small muted" style={{ marginBottom: 6 }}>
              Public reports (Tavily)
            </p>
            {lookups.length === 0 ? (
              <p>No lookups were needed.</p>
            ) : (
              <ul className="lookups">
                {lookups.map((l, i) => (
                  <li key={`${l.skill}-${i}`}>
                    <b>{SKILL_WORDS[l.skill] ?? l.skill}</b> <span className="mono">{l.value}</span>
                    {l.skipped ? (
                      <> — skipped ({l.skipped})</>
                    ) : l.hits.length === 0 ? (
                      <> — no public reports found</>
                    ) : (
                      <>
                        {" "}
                        — {l.hits.length} report{l.hits.length === 1 ? "" : "s"}:{" "}
                        {l.hits.slice(0, 3).map((h, j) => {
                          const href = safeHref(h.url);
                          return (
                            <span key={h.url}>
                              {j > 0 && ", "}
                              {href ? (
                                <a href={href} target="_blank" rel="noreferrer noopener">
                                  {hostOf(h.url)}
                                </a>
                              ) : (
                                hostOf(h.url)
                              )}
                            </span>
                          );
                        })}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
