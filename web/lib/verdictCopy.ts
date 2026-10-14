import type { CheckResponse } from "./types";

function firstName(full: string | null | undefined): string | null {
  const n = (full ?? "").trim();
  return n ? n.split(/\s+/)[0] ?? null : null;
}

/** The headline, the one-sentence instruction, and an optional supporting line, per label. */
export function copyFor(r: CheckResponse) {
  const family = r.family.name;
  const askFamily = family ? `Ask ${family} first.` : "Ask your family first.";
  switch (r.label) {
    case "red":
      return {
        title: "Scam",
        lede: "Do not reply, pay, or call.",
        sub: family && r.family.notified ? `${family} has been told.` : "Tell someone in your family.",
      };
    case "yellow":
      return {
        title: "Careful",
        lede: "This might be a scam. Do not click, pay, or share details.",
        sub: askFamily,
      };
    case "green": {
      const who = firstName(r.contact.name);
      const rel = r.contact.relationship ? `, your ${r.contact.relationship}` : "";
      const sub =
        r.contact.status === "trusted" && who
          ? `It came from ${who}${rel}, who is in your trusted contacts.`
          : r.contact.status === "known_service" && r.contact.name
            ? `It came from ${r.contact.name}, a service you already use.`
            : "Nothing in it looks like a scam.";
      return { title: "Safe", lede: "This message looks fine.", sub };
    }
  }
}
