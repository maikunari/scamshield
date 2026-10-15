import assert from "node:assert/strict";
import { test } from "node:test";
import { advance, parseSse, START } from "./progress.ts";
import type { CheckEvent } from "./types.ts";

test("parseSse returns complete events and keeps the unfinished tail", () => {
  const { events, rest } = parseSse('data: {"type":"step","step":"reading"}\n\ndata: {"type":"quick","label":"red","reason":"x"}\n\ndata: {"type":"st');
  assert.deepEqual(events, [
    { type: "step", step: "reading" },
    { type: "quick", label: "red", reason: "x" },
  ]);
  assert.equal(rest, 'data: {"type":"st');
});

test("parseSse skips a garbled event and handles CRLF", () => {
  const { events, rest } = parseSse('data: {nope\r\n\r\ndata: {"type":"error"}\r\n\r\n');
  assert.deepEqual(events, [{ type: "error" }]);
  assert.equal(rest, "");
});

test("progress reads like the check: reading, searching, reports found, verdict", () => {
  const events: CheckEvent[] = [
    { type: "step", step: "reading" },
    { type: "quick", label: "red", reason: "Asking for gift card codes is a well-known scam." },
    { type: "step", step: "searching" },
    { type: "step", step: "found", count: 5 },
    { type: "step", step: "deciding" },
  ];
  const p = events.reduce(advance, START);
  assert.deepEqual(
    p.lines.map((l) => [l.text, l.done]),
    [
      ["Reading the message", true],
      ["Checking public scam reports", true],
      ["5 public reports found", true],
      ["Making the verdict", false],
    ],
  );
  assert.deepEqual(p.quick, { label: "red", reason: "Asking for gift card codes is a well-known scam." });
});

test("no reports and one report read naturally; repeated steps are ignored", () => {
  const none = [{ type: "step", step: "found", count: 0 } as CheckEvent].reduce(advance, START);
  assert.equal(none.lines.at(-1)?.text, "No public reports found");
  const one = advance(START, { type: "step", step: "found", count: 1 });
  assert.equal(one.lines.at(-1)?.text, "1 public report found");
  const twice = advance(advance(START, { type: "step", step: "searching" }), { type: "step", step: "searching" });
  assert.equal(twice.lines.length, 2);
});
