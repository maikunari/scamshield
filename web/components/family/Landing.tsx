"use client";

import { useEffect, useState } from "react";
import { familyApi } from "@/lib/family";
import type { Gate } from "@/lib/family";
import { ShieldIcon } from "../Icons";
import { DemoLoader } from "./DemoLoader";
import "./family.css";

/** `/`: where a judge or a family member starts. The parent's own screen is `/check/`. */
export function Landing() {
  const [gate, setGate] = useState<Gate | null>(null);

  useEffect(() => {
    familyApi.gate().then(setGate).catch(() => setGate(null));
  }, []);

  return (
    <div className="fam">
      <main className="landing">
        <div className="brand">
          <ShieldIcon />
          ScamShield
        </div>
        <div className="hero">
          <h1>So your parents never face a scammer alone.</h1>
          <p>
            A parent pastes or shares a suspicious message and gets a clear answer: Scam, Careful or Safe. When it is a scam, the family is told straight away, with the evidence and calm words for the phone call.
          </p>
        </div>
        <div className="paths">
          <section className="card path">
            <h2>For the parent</h2>
            <p>One big button. Paste or share a message and get a plain answer, read aloud if you like.</p>
            <a className="fbtn fbtn--primary" href="/check/">
              Open the check page
            </a>
          </section>
          <section className="card path">
            <h2>For the family</h2>
            <p>Recent checks, the alerts that went out, how each was decided, and who she trusts.</p>
            <a className="fbtn fbtn--quiet" href="/family/">
              Open the dashboard
            </a>
          </section>
          <section className="card path path--judge" aria-labelledby="judge-h">
            <h2 id="judge-h">Judging this?</h2>
            {gate?.demo_load === false ? (
              <p>The demo family is switched off on this deployment.</p>
            ) : (
              <DemoLoader demoPassphrase={gate?.demo_passphrase ?? null} />
            )}
          </section>
        </div>
        <p className="landing-foot">
          Messages are checked with NVIDIA Nemotron models on Nebius Token Factory, and public scam reports from Tavily. Nothing else is contacted, and suspicious links are never opened.
        </p>
      </main>
    </div>
  );
}
