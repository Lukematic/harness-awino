"use strict";
// test/harnessReply.js — chat shows prose, not the contract header/footer.
const assert = require("assert");
const { harnessReplyMarkdown } = require("../webview/chat.js");
let pass = 0, fail = 0;
function t(name, input, expected) {
  try { assert.strictEqual(harnessReplyMarkdown(input), expected); pass++; console.log("ok   - " + name); }
  catch (e) { fail++; console.log("FAIL - " + name + "\n  got: " + JSON.stringify(harnessReplyMarkdown(input))); }
}
const said = [
  "[A.W.I.N.O. | phase: IDLE | mode: observe | stance: advisor | skills:  | loop: 6 | run: 9f17 | knowledge: 0/0 | mission: none]",
  "STANCE -> advisor (default)",
  "[Certain] Listing the root directory to inspect project structure.",
  "Questions: What should the mission be? | Who is it for?",
  "Assuming: [Likely] Context is in the repo",
  "Floor: IDLE | Next action: answer: What should the mission be? | Blocked on: user answers.",
].join("\n");
t("field sample from the screenshots", said,
  "[Certain] Listing the root directory to inspect project structure.\n" +
  "**Questions**\n\n- What should the mission be?\n- Who is it for?\n" +
  "**Assumptions**\n\n- [Likely] Context is in the repo\n\n" +
  "*Next:* answer: What should the mission be? · *waiting on* user answers");
t("nothing blocking omits the waiting part",
  "[A.W.I.N.O. | phase: SHIP]\nSTANCE -> premortem (x)\nShipped.\nFloor: SHIP | Next action: Mission complete. | Blocked on: nothing.",
  "Shipped.\n\n*Next:* Mission complete.");
t("plain text passes through", "Hello there.", "Hello there.");
t("empty", "", "");
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
