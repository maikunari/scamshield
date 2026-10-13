import type { Metadata } from "next";
import { BackIcon, ShieldIcon } from "@/components/Icons";

export const metadata: Metadata = { title: "Privacy - ScamShield" };

export default function PrivacyPage() {
  return (
    <main className="shell prose">
      <header className="brand">
        <ShieldIcon />
        <span>ScamShield</span>
      </header>

      <section className="hero">
        <h1>Your privacy</h1>
        <p>What ScamShield keeps, who sees it, and how to remove it.</p>
      </section>

      <section>
        <h2>What is kept</h2>
        <ul>
          <li>The messages you check, who they were from, and the answer (Scam, Careful or Safe).</li>
          <li>The steps ScamShield took to reach the answer, called the trace.</li>
          <li>A note to your family when a message looks like a scam, and whether they called you.</li>
          <li>What it remembers about you: your trusted contacts and the services you use.</li>
          <li>A message that comes back Safe is not kept word for word, unless your family chose to keep them.</li>
        </ul>
        <p>All of this is stored in ScamShield&apos;s own database. It is not sold or used for advertising.</p>
      </section>

      <section>
        <h2>Hidden before checking</h2>
        <p>
          Before any message is sent to an AI model, ScamShield hides card numbers, ID numbers (such as a Social Security or My Number) and
          anything that looks like a password or PIN. The hidden parts are never sent anywhere.
        </p>
      </section>

      <section>
        <h2>Who sees what</h2>
        <ul>
          <li>
            <strong>Nebius Token Factory</strong> runs the AI models (NVIDIA Nemotron). It sees the message text with the private parts already hidden.
          </li>
          <li>
            <strong>Tavily</strong> is a web search service used to look for reports of a scam. It sees only a phone number, a website name, or a
            short phrase from the message, with private parts hidden. It never sees the whole message.
          </li>
          <li>
            <strong>Your family</strong> is told when a message looks like a scam, by Telegram or email. The note says who it was from, why it
            looks like a scam and what to say on the phone.
          </li>
          <li>Links inside a message are never opened.</li>
        </ul>
      </section>

      <section>
        <h2>How long it is kept</h2>
        <p>
          Until you delete it. If whoever runs this copy of ScamShield has switched on automatic clean-up, checks older than the number of days they
          chose are deleted by themselves.
        </p>
      </section>

      <section>
        <h2>Delete it</h2>
        <p>You can remove everything at any time. You can also download a copy first.</p>
      </section>

      <div className="stack">
        <a className="btn btn-danger" href="/delete/">
          Delete my data
        </a>
        <a className="btn btn-secondary btn-center" href="/check/">
          <BackIcon />
          Back to ScamShield
        </a>
      </div>
    </main>
  );
}
