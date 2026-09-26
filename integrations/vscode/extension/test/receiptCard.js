"use strict";
// test/receiptCard.js — the receipt card shows promise, proof and lesson,
// labels honestly, and escapes everything it renders.
const assert = require("assert");
const { receiptCardHtml, receiptDuration } = require("../webview/chat.js");
let pass = 0, fail = 0;
function t(name, fn) {
  try { fn(); pass++; console.log("ok   - " + name); }
  catch (e) { fail++; console.log("FAIL - " + name + "\n  " + e.message); }
}
const proven = {
  status: "PROVEN",
  story: { title: "Salmon counter", type: "story", branch: "story/salmon-counter", outcome: "Counter live", time_s: 5400 },
  proof: {
    criteria: [{ criterion: "counts.csv has today's row", proof: "named in verifier verdict" },
               { criterion: "docs updated", proof: "unproven" }],
    steps: [{ index: 0, title: "Count script", forecast: "30m", forecast_s: 1800, actual_s: 1800, state: "done" },
            { index: 1, title: "CSV writer", forecast: "1h", forecast_s: 3600, actual_s: 10800, state: "done" }],
    checks: [{ cmd: "pytest", runs: 2, failures: 1, last_exit: 0 }],
    verdicts: [{ passed: true, by: "independent verifier" }],
    files: ["count.py"], commits: ["abc123 add counter"],
    journal: { events: 42, head: "0123456789abcdef", chain_ok: true },
  },
  lesson: { notes: ["Took 2.33x the forecast (3h 30m vs 1h 30m)."] },
};
t("durations", () => {
  assert.strictEqual(receiptDuration(null), "—");
  assert.strictEqual(receiptDuration(45), "45s");
  assert.strictEqual(receiptDuration(1800), "30m");
  assert.strictEqual(receiptDuration(10800), "3h 00m");
});
t("proven card shows promise, proof, lesson and actions", () => {
  const h = receiptCardHtml(proven);
  for (const s of ["Salmon counter", "rc-badge rc-ok\">PROVEN", "Counter live", "✓ counts.csv",
                   "✗ docs updated", "<td>CSV writer</td>", "class=\"rc-bad\">3h 00m",
                   "<code>pytest</code> → exit 0", "independent verifier", "1 file(s) written",
                   "chain intact", "Took 2.33x", "Copy as PR description", "Open receipt"])
    assert.ok(h.includes(s), "missing: " + s);
});
t("unknown status falls back to UNVERIFIED", () => {
  const h = receiptCardHtml({ status: "<b>TRUST ME</b>", story: { title: "x" } });
  assert.ok(h.includes("rc-bad\">UNVERIFIED"));
  assert.ok(!h.includes("TRUST ME"));
});
t("partly proven is a warning, not a pass", () => {
  assert.ok(receiptCardHtml({ status: "PARTLY PROVEN", story: {} }).includes("rc-badge rc-warn\">PARTLY PROVEN"));
});
t("untracked time says so", () => {
  assert.ok(receiptCardHtml({ status: "PROVEN", story: { time_s: 0 } }).includes("time not tracked"));
});
t("preview is labelled", () => {
  assert.ok(receiptCardHtml({ status: "UNVERIFIED", preview: true, story: {} }).includes("PREVIEW · UNVERIFIED"));
});
t("values are escaped", () => {
  const h = receiptCardHtml({ status: "PROVEN", story: { title: "<img src=x onerror=1>" },
    proof: { checks: [{ cmd: "<script>", last_exit: 0, runs: 1, failures: 0 }] } });
  assert.ok(!h.includes("<img") && !h.includes("<script>"));
  assert.ok(h.includes("&lt;img") && h.includes("&lt;script&gt;"));
});
t("empty receipt renders without throwing", () => { receiptCardHtml(undefined); receiptCardHtml({}); });
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
