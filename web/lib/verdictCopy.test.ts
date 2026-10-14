import assert from "node:assert/strict";
import { test } from "node:test";
import type { CheckResponse } from "./types.ts";
import { copyFor } from "./verdictCopy.ts";

function result(over: Partial<CheckResponse> = {}, family: Partial<CheckResponse["family"]> = {}): CheckResponse {
  return {
    id: "c1",
    label: "red",
    score: 95,
    red_flags: [],
    safe_action: "Hang up.",
    evidence_note: "",
    contact: { status: "unknown", name: null, relationship: null, reason: "" },
    family: { name: "Kenji Hayashi", notified: false, ...family },
    language: "en",
    offline: false,
    degraded: false,
    ...over,
  };
}

test("red says the family has been told only when an alert really went out", () => {
  assert.equal(copyFor(result({}, { notified: true })).sub, "Kenji Hayashi has been told.");
});

test("red falls back to a neutral line when nobody was notified (failed, rate-limited, deduplicated, unconfigured)", () => {
  assert.equal(copyFor(result({}, { notified: false })).sub, "Tell someone in your family.");
});

test("red with no family name never claims anyone was told", () => {
  assert.equal(copyFor(result({}, { name: null, notified: true })).sub, "Tell someone in your family.");
});

test("yellow and green copy does not depend on notified", () => {
  assert.equal(copyFor(result({ label: "yellow" })).sub, "Ask Kenji Hayashi first.");
  assert.equal(copyFor(result({ label: "green" })).sub, "Nothing in it looks like a scam.");
});
