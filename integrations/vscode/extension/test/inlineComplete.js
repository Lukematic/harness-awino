"use strict";
// test/inlineComplete.js — unit tests for src/inlineComplete.ts (Tab
// ghost-text completions). vscode-free; run after `npm run compile`:
//   node ./test/inlineComplete.js

const {
  extractCompletionContext,
  KeystrokeDebouncer,
  createInlineCompletionProvider,
  MAX_PREFIX_CHARS,
  MAX_SUFFIX_CHARS,
} = require("../out/inlineComplete.js");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

function fakeDoc(text, languageId = "python", file = "/w/a.py") {
  return {
    getText: () => text,
    offsetAt: (pos) => {
      const lines = text.split("\n");
      let off = 0;
      for (let i = 0; i < pos.line; i++) off += lines[i].length + 1;
      return off + pos.character;
    },
    uri: { fsPath: file, toString: () => "file://" + file },
    languageId,
  };
}
const fakeToken = () => {
  const cbs = [];
  return {
    isCancellationRequested: false,
    onCancellationRequested: (cb) => { cbs.push(cb); return { dispose() {} }; },
    _cancel() { cbs.forEach((cb) => cb()); },
  };
};

function makeDeps(overrides = {}) {
  const queries = [];
  const logs = [];
  const state = { captured: null, connected: true, nextResult: undefined };
  const deps = {
    host: {
      registerInlineCompletionItemProvider: (selector, provider) => {
        state.captured = { selector, provider };
        return { dispose: () => {} };
      },
      InlineCompletionItem: class { constructor(text) { this.insertText = text; } },
      InlineCompletionList: class { constructor(items) { this.items = items; } },
    },
    query: async (name, args, timeoutMs) => {
      queries.push({ name, args, timeoutMs });
      return state.nextResult !== undefined ? state.nextResult : { completion: "" };
    },
    isConnected: () => state.connected !== false,
    getConfig: () => ({ enable: true, debounceMs: 10, model: "" }),
    log: (m) => logs.push(m),
  };
  return {
    deps, queries, logs,
    get captured() { return state.captured; },
    get connected() { return state.connected; },
    set connected(v) { state.connected = v; },
    set nextResult(v) { state.nextResult = v; },
  };
}

async function main() {
  // ---- extractCompletionContext ----
  {
    const c = extractCompletionContext("def foo():\n    ", "def foo():\n    ".length, "/w/a.py", "python");
    ok(c !== null && c.prefix === "def foo():\n    " && c.language === "python", "extract: basic python");
  }
  {
    const c = extractCompletionContext("const x = ", 10, "/w/a.ts", "typescript");
    ok(c !== null && c.language === "typescript", "extract: typescript supported");
  }
  {
    const c = extractCompletionContext("int x = ", 8, "/w/a.c", "c");
    ok(c === null, "extract: unsupported language -> null");
  }
  {
    const c = extractCompletionContext("   \n  ", 6, "/w/a.py", "python");
    ok(c === null, "extract: whitespace-only prefix -> null");
  }
  {
    const big = "p".repeat(5000);
    const c = extractCompletionContext(big + "TAIL", 5004, "/w/a.py", "python");
    ok(c !== null && c.prefix.length === MAX_PREFIX_CHARS && c.prefix.endsWith("TAIL"),
      "extract: prefix capped at 1500, tail kept");
  }
  {
    const c = extractCompletionContext("ab", 2, "/w/a.py", "python");
    ok(c !== null && c.suffix === "", "extract: cursor at end -> empty suffix");
  }
  {
    const text = "ab" + "s".repeat(5000);
    const c = extractCompletionContext(text, 2, "/w/a.py", "python");
    ok(c !== null && c.suffix.length === MAX_SUFFIX_CHARS, "extract: suffix capped at 750");
  }
  {
    const c = extractCompletionContext("abc", 0, "/w/a.py", "python");
    ok(c === null, "extract: offset 0 -> null");
  }

  // ---- KeystrokeDebouncer ----
  {
    const d = new KeystrokeDebouncer();
    const g1 = d.trigger(30);
    const g2 = d.trigger(30); // supersedes g1
    const r2 = await g2;
    ok(r2 === 2 && d.current === 2, "debouncer: second trigger supersedes first");
    // g1's timer was cleared: it must never resolve (would be stale work)
    const raced = await Promise.race([g1.then(() => "resolved"), new Promise((r) => setTimeout(() => r("pending"), 80))]);
    ok(raced === "pending", "debouncer: superseded trigger never resolves");
    d.dispose();
  }
  {
    const d = new KeystrokeDebouncer();
    const t0 = Date.now();
    await d.trigger(40);
    ok(Date.now() - t0 >= 35, "debouncer: waits the quiet period");
    d.dispose();
  }

  // ---- provider: disabled / disconnected / empty ----
  {
    const h = makeDeps(); const deps = h.deps;
    deps.getConfig = () => ({ enable: false, debounceMs: 5, model: "" });
    const p = createInlineCompletionProvider(deps);
    const r = await h.captured.provider.provideInlineCompletionItems(
      fakeDoc("def foo():\n    ", "python"), { line: 1, character: 4 }, {}, fakeToken());
    ok(r.items.length === 0, "provider: disabled -> no items");
    p.dispose();
  }
  {
    const h = makeDeps(); const deps = h.deps;
    h.connected = false;
    const p = createInlineCompletionProvider(deps);
    const r = await h.captured.provider.provideInlineCompletionItems(
      fakeDoc("def foo():\n    ", "python"), { line: 1, character: 4 }, {}, fakeToken());
    ok(r.items.length === 0, "provider: disconnected -> no items (never fake)");
    p.dispose();
  }
  {
    const h = makeDeps(); const deps = h.deps; const queries = h.queries;
    h.nextResult = { completion: "" };
    const p = createInlineCompletionProvider(deps);
    const r = await h.captured.provider.provideInlineCompletionItems(
      fakeDoc("x = ", "python"), { line: 0, character: 4 }, {}, fakeToken());
    ok(r.items.length === 0 && queries.length === 1 && queries[0].name === "complete",
      "provider: empty completion -> no ghost text, command named 'complete'");
    p.dispose();
  }

  // ---- provider: happy path ----
  {
    const h = makeDeps(); const deps = h.deps; const queries = h.queries;
    h.nextResult = { completion: "1 + 2" };
    const p = createInlineCompletionProvider(deps);
    const r = await h.captured.provider.provideInlineCompletionItems(
      fakeDoc("x = ", "python"), { line: 0, character: 4 }, {}, fakeToken());
    ok(r.items.length === 1 && r.items[0].insertText === "1 + 2", "provider: completion -> one ghost item");
    ok(queries[0].args.prefix === "x = " && queries[0].args.language === "python",
      "provider: sends capped prefix + language");
    ok(queries[0].timeoutMs === 8000, "provider: short 8s timeout, not 120s");
    ok(!("model" in queries[0].args), "provider: no model key when override empty");
    p.dispose();
  }
  {
    const h = makeDeps(); const deps = h.deps; const queries = h.queries;
    deps.getConfig = () => ({ enable: true, debounceMs: 5, model: "tiny-fast" });
    h.nextResult = { completion: "y" };
    const p = createInlineCompletionProvider(deps);
    await h.captured.provider.provideInlineCompletionItems(
      fakeDoc("x = ", "python"), { line: 0, character: 4 }, {}, fakeToken());
    ok(queries[0].args.model === "tiny-fast", "provider: model override forwarded");
    p.dispose();
  }

  // ---- provider: stale generation discarded ----
  {
    const h = makeDeps(); const deps = h.deps; const queries = h.queries;
    deps.getConfig = () => ({ enable: true, debounceMs: 30, model: "" });
    let release;
    const gate = new Promise((res) => { release = res; });
    deps.query = async (name, args) => { queries.push({ name, args }); await gate; return { completion: "STALE" }; };
    const p = createInlineCompletionProvider(deps);
    const doc = fakeDoc("x = 1\n", "python");
    const tok1 = fakeToken();
    const first = h.captured.provider.provideInlineCompletionItems(doc, { line: 0, character: 5 }, {}, tok1);
    // second keystroke arrives while first is debouncing/querying;
    // VS Code cancels the superseded request's token.
    await new Promise((r) => setTimeout(r, 5));
    tok1._cancel();
    deps.query = async () => ({ completion: "FRESH" });
    const second = h.captured.provider.provideInlineCompletionItems(doc, { line: 0, character: 5 }, {}, fakeToken());
    release();
    const [r1, r2] = await Promise.all([first, second]);
    ok(r1.items.length === 0, "provider: stale in-flight completion discarded");
    ok(r2.items.length === 1 && r2.items[0].insertText === "FRESH", "provider: latest keystroke wins");
    p.dispose();
  }

  // ---- provider: query error -> empty, logged, typing unbroken ----
  {
    const h = makeDeps(); const deps = h.deps; const logs = h.logs;
    deps.query = async () => { throw new Error("boom"); };
    const p = createInlineCompletionProvider(deps);
    const r = await h.captured.provider.provideInlineCompletionItems(
      fakeDoc("x = ", "python"), { line: 0, character: 4 }, {}, fakeToken());
    ok(r.items.length === 0 && logs.length === 1 && logs[0].includes("boom"),
      "provider: query error -> no items, logged to output channel");
    p.dispose();
  }
  {
    const h = makeDeps(); const deps = h.deps;
    const p = createInlineCompletionProvider(deps);
    const sel = h.captured.selector;
    ok(Array.isArray(sel) && sel.some((s) => s.language === "python") && sel.some((s) => s.language === "typescript"),
      "provider: registered for python + typescript-family");
    p.dispose();
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
