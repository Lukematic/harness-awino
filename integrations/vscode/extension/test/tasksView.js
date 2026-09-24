"use strict";
// test/tasksView.js — unit tests for the 0.4.1 TasksView (src/views.ts).
//
// The Tasks panel is a READ-ONLY mirror of the harness registry tracker:
// task states change only in code (registry.set_task_state on verified
// completion) — the view never invents, edits, or checks off tasks.
// views.ts imports "vscode" at runtime (TreeItem, EventEmitter), so it is
// stubbed here with minimal classes and the sidecar query is injected as
// a plain fake. Run after `npm run compile`:
//   node ./test/tasksView.js

const Module = require("module");
const origLoad = Module._load;

class StubTreeItem {
  constructor(label, collapsibleState) {
    this.label = label;
    this.collapsibleState = collapsibleState;
    this.description = undefined;
    this.tooltip = undefined;
  }
}
class StubEventEmitter {
  constructor() {
    this._listeners = [];
    this.event = (fn) => {
      this._listeners.push(fn);
      return { dispose() {} };
    };
  }
  fire(arg) {
    this._listeners.forEach((fn) => fn(arg));
  }
}
Module._load = function (request, parent, isMain) {
  if (request === "vscode") {
    return {
      TreeItem: StubTreeItem,
      TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 },
      EventEmitter: StubEventEmitter,
    };
  }
  return origLoad.call(this, request, parent, isMain);
};

const { TasksView, Group } = require("../out/views.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

function fakeQuery(result) {
  const calls = [];
  const q = async (name, args) => {
    calls.push({ name, args });
    if (result instanceof Error) throw result;
    return result;
  };
  return { q, calls };
}

const FIXTURE = {
  attached: true,
  tasks: [
    { id: "t1", text: "Wire tasks_list", state: "doing", source: "seed", done_criteria: "query works", depends_on: [], evidence: ["e1"] },
    { id: "t2", text: "Write docs", state: "open", source: "manual", done_criteria: "", depends_on: ["t1"], evidence: [] },
    { id: "t3", text: "Fix flake", state: "blocked", source: "seed", done_criteria: "green twice", depends_on: [], evidence: [] },
    { id: "t4", text: "Ship 0.4.0", state: "done", source: "manual", done_criteria: "", depends_on: [], evidence: ["e1", "e2"] },
  ],
};

async function main() {
  // 1. groups by state in doing/open/blocked/done order, with state marks
  {
    const { q, calls } = fakeQuery(FIXTURE);
    const kids = await new TasksView(q).getChildren();
    ok(kids.length === 4, "four state groups rendered");
    ok(kids[0].label === "doing (1)" && kids[1].label === "open (1)" &&
       kids[2].label === "blocked (1)" && kids[3].label === "done (1)",
      "groups ordered doing/open/blocked/done");
    const doingKids = await new TasksView(q).getChildren(kids[0]);
    ok(doingKids[0].label.indexOf("\u25D0") === 0, "doing task marked \u25D0");
    const openKids = await new TasksView(q).getChildren(kids[1]);
    ok(openKids[0].label.indexOf("\u25CB") === 0, "open task marked \u25CB");
    const blockedKids = await new TasksView(q).getChildren(kids[2]);
    ok(blockedKids[0].label.indexOf("\u2715") === 0, "blocked task marked \u2715");
    const doneKids = await new TasksView(q).getChildren(kids[3]);
    ok(doneKids[0].label.indexOf("\u2611") === 0, "done task marked \u2611");
    ok(calls.length === 1 && calls[0].name === "tasks_list",
      "read-only: the view issues exactly one tasks_list query, nothing else");
  }
  // 2. tooltip carries the audit trail; description shows source + criteria
  {
    const { q } = fakeQuery(FIXTURE);
    const kids = await new TasksView(q).getChildren();
    const doingKids = await new TasksView(q).getChildren(kids[0]);
    const tip = doingKids[0].tooltip;
    ok(tip.indexOf("read-only") >= 0, "tooltip says the view is read-only");
    ok(tip.indexOf("id: t1") >= 0, "tooltip names the task id");
    ok(tip.indexOf("evidence: 1 item(s)") >= 0, "tooltip counts evidence");
    ok(doingKids[0].description.indexOf("seed") >= 0, "description shows the task source");
    ok(doingKids[0].description.indexOf("query works") >= 0, "description shows done criteria");
  }
  // 3. registry not attached yet: honest empty state, never a stale list
  {
    const { q } = fakeQuery({ attached: false, tasks: [] });
    const kids = await new TasksView(q).getChildren();
    ok(kids.length === 1 && kids[0].label.indexOf("no mission yet") >= 0,
      "unattached registry shows the no-mission message, not a fabricated list");
  }
  // 4. attached but no tasks tracked
  {
    const { q } = fakeQuery({ attached: true, tasks: [] });
    const kids = await new TasksView(q).getChildren();
    ok(kids.length === 1 && kids[0].label === "(no tasks tracked yet)",
      "attached-but-empty registry shows the no-tasks message");
  }
  // 5. sidecar failure degrades to a leaf, never throws
  {
    const { q } = fakeQuery(new Error("boom"));
    const kids = await new TasksView(q).getChildren();
    ok(kids.length === 1 && kids[0].label === "sidecar unavailable",
      "query failure renders 'sidecar unavailable' instead of throwing");
  }
  // 6. unexpected states land in an "other" group rather than vanishing
  {
    const { q } = fakeQuery({
      attached: true,
      tasks: [{ id: "t9", text: "Mystery", state: "weird", source: "", done_criteria: "", depends_on: [], evidence: [] }],
    });
    const kids = await new TasksView(q).getChildren();
    ok(kids.length === 1 && kids[0].label === "other (1)",
      "unknown task state grouped under 'other'");
    ok(kids[0] instanceof Group, "'other' is an expandable group");
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
