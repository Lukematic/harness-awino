"use strict";
// test/chatHistory.js — unit tests for src/chatHistory.ts
// (spec: tab-switch must not wipe the chat transcript, 0.5.3).
const { ChatHistory, CHAT_HISTORY_MAX } = require("../out/chatHistory.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log(`ok - ${name}`); }
  else { fail++; console.error(`FAIL - ${name}`); }
}

// 1. push + replay restores every message in order (the tab-switch path)
{
  const h = new ChatHistory();
  const sent = [];
  h.push({ type: "event", payload: { event: "turn_result", n: 1 } });
  h.push({ type: "event", payload: { event: "turn_result", n: 2 } });
  h.push({ type: "ready", ready: { project: "demo" } });
  h.replay((m) => sent.push(m));
  ok(sent.length === 3, "replay restores all three messages");
  ok(sent[0].payload.n === 1 && sent[2].type === "ready", "replay preserves order and shape");
  ok(h.size === 3, "size tracks pushes");
}

// 2. empty history replays nothing (fresh install path)
{
  const h = new ChatHistory();
  const sent = [];
  h.replay((m) => sent.push(m));
  ok(sent.length === 0, "empty history replays nothing");
}

// 3. bounded: beyond the cap the oldest messages are evicted
{
  const h = new ChatHistory();
  for (let i = 0; i < CHAT_HISTORY_MAX + 50; i++) h.push({ n: i });
  ok(h.size === CHAT_HISTORY_MAX, "history is capped at CHAT_HISTORY_MAX");
  const got = [];
  h.replay((m) => got.push(m));
  ok(got[0].n === 50, "eviction drops the oldest first");
  ok(got[got.length - 1].n === CHAT_HISTORY_MAX + 49, "newest message retained");
}

// 4. entries() returns a copy, not the live log
{
  const h = new ChatHistory();
  h.push({ a: 1 });
  const e = h.entries();
  e.push({ a: 2 });
  ok(h.size === 1, "entries() does not expose the live log");
}

console.log(`\nchatHistory: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
