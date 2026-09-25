"use strict";
// test/storiesView.js — StoriesView groups stories by status and lists the
// brag board (done, newest first) with date, time dedicated and outcome.
const Module = require("module");
const origLoad = Module._load;
class StubTreeItem {
  constructor(label, collapsibleState) { this.label = label; this.collapsibleState = collapsibleState; }
}
class StubEventEmitter {
  constructor() { this.event = () => ({ dispose() {} }); }
  fire() {}
}
Module._load = function (request, parent, isMain) {
  if (request === "vscode") {
    return { TreeItem: StubTreeItem, TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 }, EventEmitter: StubEventEmitter };
  }
  return origLoad.call(this, request, parent, isMain);
};
const { StoriesView, StoryItem, Group, formatDuration } = require("../out/views.js");

let pass = 0, fail = 0;
function ok(cond, name) { if (cond) { pass++; console.log("ok   - " + name); } else { fail++; console.log("FAIL - " + name); } }

(async () => {
  const ledger = {
    stories: [
      { id: "a", title: "Salmon counter", status: "doing", time_s: 5400 },
      { id: "b", title: "Docs", status: "open", ready_to_close: true, time_s: 60 },
      { id: "c", title: "Deep health", status: "parked", revisit_due: true },
      { id: "d", title: "Login fix", status: "done", closed_ts: 1790000000, outcome: "shipped", time_s: 7200 },
    ],
    brag: [{ id: "d", title: "Login fix", status: "done", closed_ts: 1790000000, outcome: "shipped", time_s: 7200 }],
    attached: true,
  };
  const view = new StoriesView(async (name) => { ok(name === "stories", "queries the stories command"); return ledger; });
  const top = await view.getChildren();
  const names = top.map((g) => g.label);
  ok(JSON.stringify(names) === JSON.stringify(["In progress (1)", "Open (1)", "Parked ideas (1)", "Brag board (1)"]), "groups in order: " + names.join(", "));
  const open = await view.getChildren(top[1]);
  ok(open[0] instanceof StoryItem && open[0].story.id === "b", "story item carries its id");
  ok(open[0].contextValue === "awinoStory", "open story gets the action menu");
  ok(String(open[0].description).includes("ready to close"), "ready-to-close flag shown");
  const parked = await view.getChildren(top[2]);
  ok(String(parked[0].description).includes("revisit due"), "revisit-due flag shown");
  const brag = await view.getChildren(top[3]);
  ok(brag[0].contextValue === "awinoStoryDone", "done story has no close action");
  ok(/\d{4}-\d{2}-\d{2} · 2h 0m · shipped/.test(brag[0].description), "brag row: date · time · outcome (" + brag[0].description + ")");
  ok(formatDuration(59) === "<1m" && formatDuration(0) === "0m" && formatDuration(3660) === "1h 1m", "duration formatting");

  const empty = new StoriesView(async () => ({ stories: [], brag: [], attached: false }));
  const e = await empty.getChildren();
  ok(e.length === 1 && String(e[0].label).includes("no stories yet"), "empty ledger hint");
  const broken = new StoriesView(async () => { throw new Error("down"); });
  const b = await broken.getChildren();
  ok(String(b[0].label).includes("sidecar unavailable"), "sidecar failure shown, not an empty list");

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
