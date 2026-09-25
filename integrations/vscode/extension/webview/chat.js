/* chat.js — Awino mission chat renderer (plain JS, no build).
 * Renders every §4 sidecar event; forwards user input via acquireVsCodeApi().
 * The webview never constructs turns or calls tools — it only displays
 * events and sends the §4 command verbs.
 */

/* ============================================================================
 * AwinoMarkdown — dependency-free markdown renderer (spec §4.2–§4.4).
 *
 * Subset (in order): fenced code blocks (```lang, incl. ```diff), ATX
 * headings #/##/### -> h3/h4/h5 (capped), **bold**, *italic*, `inline code`,
 * unordered/ordered lists (2-level nesting), [text](https://…) links
 * (http/https ONLY — javascript: and anything else stays plain text),
 * > blockquotes, --- -> <hr>, blank-line paragraphs -> <p>.
 * Tables are NOT supported: a pipe table renders inside a code block.
 *
 * Security: HTML is escaped first; fenced code is extracted before any
 * other parsing so code contents are never markdown-processed (and are
 * escaped at render time). Link hrefs accept http/https only, and quotes
 * are excluded from URLs so a href attribute can never be broken out of.
 * ========================================================================== */

var __awinoCbSeq = 0;

function __mdEscape(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* §4.4 diff renderer. Input is already HTML-escaped. One display:block span
 * per line, joined with NO whitespace — the spans are block-level so no
 * inter-line whitespace is needed, and none is emitted (otherwise <pre>
 * would render phantom blank lines between them). */
function __renderDiff(escapedCode) {
  return escapedCode.split("\n").map(function (ln) {
    if (ln.indexOf("@@") === 0) return '<span class="diff-hunk">' + ln + "</span>";
    var ch = ln.charAt(0);
    if (ch === "+" && ln.charAt(1) !== "+")
      return '<span class="diff-add"><span class="dm">' + ch + "</span>" + ln.slice(1) + "</span>";
    if (ch === "-" && ln.charAt(1) !== "-")
      return '<span class="diff-del"><span class="dm">' + ch + "</span>" + ln.slice(1) + "</span>";
    return '<span class="diff-ctx">' + ln + "</span>";
  }).join("");
}

/* §4.3 code block with header bar, language label, Copy button. */
function __renderCodeBlock(lang, code) {
  var id = "awino-cb-" + (++__awinoCbSeq);
  var label = __mdEscape(lang || "text");
  var escaped = __mdEscape(code);
  var body = (String(lang || "").toLowerCase() === "diff") ? __renderDiff(escaped) : escaped;
  return '<div class="codeblock"><div class="codeblock-header">' +
    '<span class="codeblock-lang">' + label + "</span>" +
    '<button type="button" class="copy-btn">⧉ Copy</button></div>' +
    '<pre class="codeblock-body" id="' + id + '">' + body + "</pre></div>";
}

/* Inline pass. Input is already HTML-escaped. Inline code spans are stashed
 * first so *, ** and link syntax inside code stays literal. */
function __mdInline(s) {
  var codes = [];
  s = String(s).replace(/`([^`\n]+)`/g, function (m, c) {
    codes.push(c);
    return "\x01" + (codes.length - 1) + "\x02";
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s"]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/\x01(\d+)\x02/g, function (m, n) {
    return "<code>" + codes[+n] + "</code>";
  });
  return s;
}

function __isCodePlaceholder(line) {
  return line.charAt(0) === "\x00" && /^\x00CODEBLOCK\x00\d+\x00$/.test(line);
}

function __isTableStart(lines, i) {
  if (!/\|/.test(lines[i])) return false;
  var sep = lines[i + 1];
  return !!sep && /^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/.test(sep);
}

/* Lists: "-" / "*" unordered, "1." / "1)" ordered, 2-level nesting. */
function __renderList(lines, start) {
  var html = "";
  var stack = []; // {tag, indent}
  var i = start;
  while (i < lines.length) {
    var m = lines[i].match(/^(\s*)([-*]|\d+[.)])\s+(.*)$/);
    if (!m) break;
    var indent = m[1].replace(/\t/g, "  ").length;
    var tag = /^\d/.test(m[2]) ? "ol" : "ul";
    var item = __mdInline(__mdEscape(m[3]));
    while (stack.length && indent < stack[stack.length - 1].indent) {
      html += "</li></" + stack.pop().tag + ">";
    }
    var top = stack[stack.length - 1];
    if (!top || (indent > top.indent && stack.length < 2)) {
      html += "<" + tag + "><li>" + item;
      stack.push({ tag: tag, indent: indent });
    } else if (tag !== top.tag) {
      html += "</li></" + stack.pop().tag + "><" + tag + "><li>" + item;
      stack.push({ tag: tag, indent: indent });
    } else {
      html += "</li><li>" + item;
    }
    i++;
  }
  while (stack.length) html += "</li></" + stack.pop().tag + ">";
  return { html: html, next: i };
}

function AwinoMarkdown(src) {
  var text = String(src == null ? "" : src).replace(/\r\n?/g, "\n");

  /* Phase 1 — extract fenced code blocks before any other parsing, so their
   * contents are never markdown-processed. (Spec: "escape HTML first, then
   * fenced code blocks" — net effect is identical: code is fully escaped
   * and never re-parsed.) */
  var blocks = [];
  text = text.replace(/(^|\n)```([^\n]*)\n([\s\S]*?)(```|$)/g, function (m, p1, lang, code) {
    blocks.push({ lang: lang.trim(), code: code.replace(/\n$/, "") });
    return p1 + "\x00CODEBLOCK\x00" + (blocks.length - 1) + "\x00";
  });

  var lines = text.split("\n");
  var html = "";
  var i = 0;
  while (i < lines.length) {
    var line = lines[i];
    if (/^\s*$/.test(line)) { i++; continue; }
    if (__isCodePlaceholder(line)) {
      var b = blocks[+line.match(/\d+/)[0]];
      html += __renderCodeBlock(b.lang, b.code);
      i++;
      continue;
    }
    if (/^---\s*$/.test(line)) { html += "<hr>"; i++; continue; }
    var hm = line.match(/^(#{1,3})\s+(.*)$/);
    if (hm) {
      var lvl = hm[1].length + 2;
      html += "<h" + lvl + ">" + __mdInline(__mdEscape(hm[2])) + "</h" + lvl + ">";
      i++;
      continue;
    }
    if (/^#{4,}\s+/.test(line)) {
      html += "<h5>" + __mdInline(__mdEscape(line.replace(/^#{4,}\s+/, ""))) + "</h5>";
      i++;
      continue;
    }
    if (/^>\s?/.test(line)) {
      var q = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) { q.push(lines[i].replace(/^>\s?/, "")); i++; }
      html += "<blockquote>" + AwinoMarkdown(q.join("\n")) + "</blockquote>";
      continue;
    }
    if (__isTableStart(lines, i)) {
      var t = [];
      while (i < lines.length && /\|/.test(lines[i])) { t.push(lines[i]); i++; }
      html += __renderCodeBlock("table", t.join("\n"));
      continue;
    }
    if (/^(\s*)([-*]|\d+[.)])\s+/.test(line)) {
      var r = __renderList(lines, i);
      html += r.html;
      i = r.next;
      continue;
    }
    /* Paragraph: consecutive plain lines joined with a space. */
    var p = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) &&
           !__isCodePlaceholder(lines[i]) && !/^---\s*$/.test(lines[i]) &&
           !/^#{1,}\s/.test(lines[i]) && !/^>\s?/.test(lines[i]) &&
           !/^(\s*)([-*]|\d+[.)])\s+/.test(lines[i]) && !__isTableStart(lines, i)) {
      p.push(lines[i]);
      i++;
    }
    html += "<p>" + __mdInline(__mdEscape(p.join(" "))) + "</p>";
  }
  return html;
}

/* Reconstruct the exact source text of a rendered code block for copying.
 * Diff blocks are one display:block span per line (no inter-line whitespace
 * in the markup); other blocks are plain text. */
function __codeBlockText(pre) {
  var kids = pre.children;
  if (kids && kids.length) {
    var parts = [];
    for (var k = 0; k < kids.length; k++) parts.push(kids[k].textContent);
    return parts.join("\n");
  }
  return pre.textContent;
}

/* §4.3 Copy wiring: attach to every .copy-btn under root. Uses
 * navigator.clipboard.writeText with a document.execCommand("copy")
 * fallback; the label flips to "Copied ✓" for 1.5 s. */
AwinoMarkdown.wireCopyButtons = function (root) {
  if (!root || !root.querySelectorAll) return;
  var btns = root.querySelectorAll(".copy-btn");
  for (var k = 0; k < btns.length; k++) (function (btn) {
    if (btn.__awinoCopyWired) return;
    btn.__awinoCopyWired = true;
    btn.addEventListener("click", function () {
      var box = btn.closest ? btn.closest(".codeblock") : null;
      var pre = box ? box.querySelector(".codeblock-body") : null;
      var text = pre ? __codeBlockText(pre) : "";
      var done = function () {
        var old = btn.innerHTML;
        btn.textContent = "Copied ✓";
        btn.classList.add("copied");
        setTimeout(function () { btn.innerHTML = old; btn.classList.remove("copied"); }, 1500);
      };
      var fallback = function () {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (e) { /* clipboard unavailable */ }
        document.body.removeChild(ta);
      };
      try {
        if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { fallback(); done(); });
        } else { fallback(); done(); }
      } catch (e) { fallback(); done(); }
    });
  })(btns[k]);
};

/* Test hook: reset the code-block id counter so fixtures are deterministic. */
AwinoMarkdown._resetIds = function () { __awinoCbSeq = 0; };

if (typeof window !== "undefined") window.AwinoMarkdown = AwinoMarkdown;
if (typeof module !== "undefined" && module.exports) module.exports = { AwinoMarkdown: AwinoMarkdown };

// The chat UI boots only inside a VS Code webview (acquireVsCodeApi +
// document). Under node this file just exports AwinoMarkdown for the
// fixture tests in test/markdown.js.
if (typeof acquireVsCodeApi === "function" && typeof document !== "undefined") {
(function () {
  const vscode = acquireVsCodeApi();
  const messages = document.getElementById("messages");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const stopBtn = document.getElementById("stop");
  const statusline = document.getElementById("statusline");
  const banner = document.getElementById("banner");
  const modelsBtn = document.getElementById("models-btn");
  const setupCard = document.getElementById("setup-card");
  const setupSub = document.getElementById("setup-sub");
  const setupBtn = document.getElementById("setup-btn");
  const providerPill = document.getElementById("provider-pill");
  const inputbarEl = document.getElementById("inputbar");
  // Spec 1.4 header elements (status dot, mode selector, new-mission).
  const connDot = document.getElementById("conn-dot");
  const modeSelect = document.getElementById("mode-select");
  const newMissionBtn = document.getElementById("new-mission-btn");
  // Spec 2.3 echo demo banner (one line above the input bar).
  const echoBanner = document.getElementById("echo-banner");
  // 0.4.1: persistent mission header + session-resume block (may be absent
  // in older test shims — guarded).
  const missionHeader = document.getElementById("mission-header");
  const sessionResumeEl = document.getElementById("session-resume");
  // Onboarding wizard elements (may be absent in older test shims — guarded).
  const wizardEl = document.getElementById("wizard");
  const wProvider = document.getElementById("w-provider");
  const wDocs = document.getElementById("w-docs");
  const wKeyStep = document.getElementById("w-keystep");
  const wKey = document.getElementById("w-key");
  const wKeylabel = document.getElementById("w-keylabel");
  const wGetKey = document.getElementById("w-getkey");
  const wKeyRow = document.getElementById("w-keyrow");
  const wGetKeyRow = document.getElementById("w-getkeyrow");
  const wBedrockAuthRow = document.getElementById("w-bedrockauthrow");
  const wBedrockAuth = document.getElementById("w-bedrockauth");
  const wBedrockProfileRow = document.getElementById("w-bedrockprofilerow");
  const wBedrockProfile = document.getElementById("w-bedrockprofile");
  const wModel = document.getElementById("w-model");
  const wModelSelect = document.getElementById("w-modelselect");
  const wIntel = document.getElementById("w-intel");
  const wEndpoint = document.getElementById("w-endpoint");
  const wRegionRow = document.getElementById("w-regionrow");
  const wRegion = document.getElementById("w-region");
  const wFetchRow = document.getElementById("w-fetchrow");
  const wFetch = document.getElementById("w-fetch");
  const wFetchNote = document.getElementById("w-fetchnote");
  const wDone = document.getElementById("w-done");
  const wSkip = document.getElementById("w-skip");
  // Spec 2.1 Step 3: Prove-it elements.
  const wProveStep = document.getElementById("w-provestep");
  const wProve = document.getElementById("w-prove");
  const wProveNote = document.getElementById("w-provenote");
  // Spec 2.1: one-line provider blurb under the chooser (echo truthfulness).
  const wBlurb = document.getElementById("w-blurb");
  const wError = document.getElementById("w-error");
  let turnInFlight = false;

  // Shared provider catalogue / model-intel (webview/setup-shared.js).
  // Lazily accessed so a failed load degrades instead of killing the chat.
  function setupMeta() {
    return (typeof AwinoSetup !== "undefined" && AwinoSetup) || null;
  }

  // ---------- theme variants (user refinement 2026-09-23) ----------
  // Two Wakandan variants: "vibranium" (lifted dark) and "savanna" (warm
  // light). Default follows the VS Code color theme (light editor ->
  // savanna, dark editor -> vibranium); the header toggle sets a manual
  // override persisted in webview state for the webview's lifetime.
  const themeToggle = document.getElementById("theme-toggle");
  const themeName = document.getElementById("theme-name");
  function readSavedTheme() {
    try {
      const st = vscode.getState && vscode.getState();
      if (st && (st.awinoTheme === "savanna" || st.awinoTheme === "vibranium")) return st.awinoTheme;
    } catch (e) { /* ignore */ }
    return null;
  }
  function defaultTheme() {
    return document.body.classList.contains("vscode-light") ? "savanna" : "vibranium";
  }
  function applyTheme(v) {
    document.body.classList.remove("awino-savanna", "awino-vibranium");
    document.body.classList.add("awino-" + v);
    if (themeName) themeName.textContent = v === "savanna" ? "Savanna" : "Vibranium";
  }
  function currentTheme() { return readSavedTheme() || defaultTheme(); }
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      const next = currentTheme() === "savanna" ? "vibranium" : "savanna";
      try {
        const prev = (vscode.getState && vscode.getState()) || {};
        prev.awinoTheme = next;
        if (vscode.setState) vscode.setState(prev);
      } catch (e) { /* ignore */ }
      applyTheme(next);
    });
  }
  applyTheme(currentTheme());

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function addMsg(cls, html) {
    const d = document.createElement("div");
    d.className = "msg " + cls;
    d.innerHTML = html;
    messages.appendChild(d);
    pinScroll();
    return d;
  }

  function chips(result) {
    const out = [];
    if (result.phase) out.push('<span class="chip phase">' + esc(result.phase) + "</span>");
    const m = result.active_mode || {};
    if (m.id) out.push('<span class="chip mode">mode: ' + esc(m.id) + "</span>");
    const p = result.persona;
    if (p && p.skill) out.push('<span class="chip persona">persona: ' + esc(p.skill) + "</span>");
    if (result.status) out.push('<span class="chip">' + esc(result.status) + "</span>");
    return out.length ? '<div class="chips">' + out.join("") + "</div>" : "";
  }

  // ---------- streaming state machine (§4.1) ----------
  const streams = new Map(); // turn_id -> stream state
  const THINK_CAP = 8000;
  const SAID_MD_LIMIT = 50000; // beyond: append-only plain text (documented degradation)

  function mk(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function tnode(s) { return document.createTextNode(s); }

  // Kimoyo bead cluster in the statusline (§2.8)
  const beadEls = {};
  const slText = mk("span", "sl-text", "not connected");
  (function bootBeads() {
    const bar = mk("span", "beads");
    ["link", "turn", "approval", "mode", "persona"].forEach(function (k) {
      const b = mk("span", "bead");
      b.title = k + ": \u2014";
      bar.appendChild(b);
      beadEls[k] = b;
    });
    statusline.innerHTML = "";
    statusline.appendChild(bar);
    statusline.appendChild(tnode(" "));
    statusline.appendChild(slText);
  })();
  function setBead(which, state, title) {
    const b = beadEls[which];
    if (!b) return;
    b.className = "bead" + (state ? " " + state : "");
    b.title = title;
  }
  function setStatusText(t) { slText.textContent = t; }

  // Setup card: when the active provider needs an API key that is not in
  // SecretStorage, surface "No model connected" at the top of the chat with
  // a button that opens the Models & Providers panel. Keys never live in
  // settings JSON — that is why there is no key field in Settings.
  function setSetupCard(show, provider) {
    if (!setupCard) return;
    setupCard.hidden = !show;
    if (show && setupSub) {
      setupSub.textContent =
        "The \"" + String(provider || "unknown") + "\" provider needs an API key before a model can connect. " +
        "Keys are stored in VS Code SecretStorage — never in settings JSON.";
    }
  }

  // Provider status pill in the input area (Kilo's "No providers" pattern):
  // the binding state where the user types — "No provider" or
  // "provider · model". Clicking opens Models & Providers.
  function setProviderPill(m) {
    if (!providerPill) return;
    var none = true;
    var label = "No provider";
    var title = "No provider connected — open Models & Providers";
    var isEcho = false;
    var stale = m.settingsDirty === true;
    if (m.keyMissing !== true && (m.connected === true || (m.ready && m.ready.binding))) {
      var b = (m.ready && m.ready.binding) || {};
      // The live binding is authoritative (Spec 4.1); m.provider is only a
      // fallback for the key-missing setup surface.
      var p = b.provider || m.provider;
      var mo = b.model || m.model;
      if (p) {
        none = false;
        isEcho = String(p).toLowerCase() === "echo";
        // Spec 2.3: the pill reads "Echo (demo)", never just "Echo".
        var pLabel = isEcho ? "Echo (demo)" : p;
        // Spec 4.4: model truncated to 24 chars with ellipsis.
        var mLabel = mo ? String(mo) : "?";
        if (mLabel.length > 24) mLabel = mLabel.slice(0, 23) + "…";
        label = pLabel + " · " + mLabel + (stale ? " (stale)" : "");
        title = "provider: " + p + " · model: " + (mo || "?") + " — open Models & Providers";
      }
    }
    providerPill.textContent = label;
    providerPill.title = title;
    if (providerPill.classList && providerPill.classList.toggle) {
      providerPill.classList.toggle("none", none);
      providerPill.classList.toggle("stale", stale && !none);
    }
    // Spec 2.3: one-line echo banner directly above the input bar.
    if (echoBanner) {
      if (isEcho && !none) {
        echoBanner.innerHTML = "Echo is a local demo — replies are canned, no AI is involved. " +
          "<a id=\"echo-switch\" href=\"#\">Switch to a real provider</a>";
        echoBanner.classList.add("show");
        var sw = document.getElementById("echo-switch");
        if (sw) sw.addEventListener("click", function (e) {
          if (e && e.preventDefault) e.preventDefault();
          vscode.postMessage({ type: "models" });
        });
      } else {
        echoBanner.classList.remove("show");
        echoBanner.innerHTML = "";
      }
    }
  }

  // ---------- persistent mission header (0.4.1) ----------
  // One line from the live sidecar status query: mission, phase, verified
  // done-criteria x/y, mission revision. Hidden when there is no mission.
  // Pure render of what the harness reports — never edited here.
  function setMissionHeader(st) {
    if (!missionHeader) return;
    var mission = st && st.mission;
    if (!mission) {
      missionHeader.hidden = true;
      missionHeader.innerHTML = "";
      return;
    }
    var crit = (st && st.criteria) || [];
    var done = 0;
    for (var i = 0; i < crit.length; i++) {
      if (crit[i] && crit[i].ok) done++;
    }
    var phase = String((st && st.phase) || "?").toUpperCase();
    var rev = st && st.mission_revision != null ? st.mission_revision : null;
    var label = String(mission);
    if (label.length > 90) label = label.slice(0, 87) + "...";
    missionHeader.innerHTML =
      '<span class="mh-phase">' + esc(phase) + "</span> &middot; " + esc(label) +
      ' &middot; <span class="mh-crit">' + done + "/" + crit.length + " done</span>" +
      (rev !== null ? " &middot; rev " + esc(rev) : "");
    missionHeader.hidden = false;
  }

  // ---------- session-focus/resume (0.4.1) ----------
  // Read-only reconstruction from the event log: mission, phase, verified
  // criteria, last progress deltas, next expected action. Rendered once on
  // (re)connect and on demand via the command palette. Nothing here is
  // editable — it mirrors exactly what the harness believes.
  function renderSessionResume(s) {
    if (!sessionResumeEl) return;
    s = s || {};
    if (!s.mission && !(s.turns > 0)) {
      sessionResumeEl.hidden = true;
      sessionResumeEl.innerHTML = "";
      return;
    }
    function row(label, html) {
      return '<div class="sr-row"><b>' + esc(label) + ":</b> " + html + "</div>";
    }
    var out = '<div class="sr-title">Session resume</div>';
    out += row("Mission", esc(s.mission || "(none)"));
    out += row(
      "Phase",
      esc(String(s.phase || "?").toUpperCase()) +
        (s.mission_revision != null ? " &middot; rev " + esc(s.mission_revision) : "")
    );
    out += row("Verified", esc(s.criteria_verified) + "/" + esc(s.criteria_total) + " done criteria");
    var labels = s.verified_labels || [];
    if (labels.length) {
      out += row("Done", labels.map(esc).join("; "));
    }
    var prog = s.last_progress || [];
    if (prog.length) {
      out += row(
        "Last progress",
        prog
          .map(function (p) {
            return esc(p);
          })
          .join("<br>")
      );
    }
    if (s.next_action) out += row("Next", esc(s.next_action));
    if (s.last_stop_point) out += row("Stopped at", esc(s.last_stop_point));
    var ms = s.recent_milestones || [];
    if (ms.length) {
      out += row(
        "Milestones",
        ms
          .map(function (mm) {
            return esc(mm.kind) + ": " + esc(mm.text);
          })
          .join("<br>")
      );
    }
    sessionResumeEl.innerHTML = out;
    sessionResumeEl.hidden = false;
  }

  // ---------- first-run onboarding wizard (§2.11) ----------
  // On first activation with no provider key configured (a key the active
  // provider actually needs), the sidebar IS the setup flow: Choose
  // provider → API key (+ Get-key button) → Model (with Fetch) → Done.
  // It supersedes the setup card while active; the input bar is parked
  // until Done/Skip. Completing or skipping records awino.onboarded so this
  // runs once.
  var wizardActive = false;
  var WIZ_MANUAL = "__awino_wmanual__";
  var wizLastSelect = "";
  var wizDocsUrl = null;

  function setWizardError(t) {
    if (!wError) return;
    wError.hidden = !t;
    wError.textContent = t || "";
  }

  // Busy state for Save & Connect: the secret store can take seconds (or
  // fail), so the button must show work is happening and never double-fire.
  var WIZ_SAVE_LABEL = "Save & Connect";
  function setWizardSaving(saving) {
    if (!wDone) return;
    wDone.disabled = saving;
    wDone.textContent = saving ? "Saving…" : WIZ_SAVE_LABEL;
  }
  function resetWizardSaveButton() {
    setWizardSaving(false);
  }

  function currentWizardModel() {
    if (wModelSelect && !wModelSelect.hidden && wModelSelect.value !== WIZ_MANUAL) {
      return wModelSelect.value;
    }
    return wModel ? wModel.value.trim() : "";
  }

  function showWizardModelInput() {
    if (!wModel || !wModelSelect) return;
    wModel.hidden = false;
    wModelSelect.hidden = true;
  }

  function updateWizardIntel() {
    if (!wIntel) return;
    var sm = setupMeta();
    var id = currentWizardModel();
    var line = sm ? sm.modelIntelLine(id) : null;
    if (!id) {
      wIntel.textContent = "Pick a model to see context-window and pricing estimates.";
    } else if (line) {
      wIntel.textContent = line;
    } else {
      wIntel.textContent = "Context/pricing unknown for this model \u2014 check the provider docs.";
    }
  }

  function updateWizardForProvider() {
    if (!wProvider) return;
    var sm = setupMeta();
    var pid = wProvider.value;
    var p = sm ? sm.providerById(pid) : null;
    var needs = sm ? sm.needsKey(pid) : (pid === "openai" || pid === "anthropic" || pid === "bedrock");
    if (wDocs) {
      if (p && p.docsUrl) {
        wDocs.hidden = false;
        wizDocsUrl = p.docsUrl;
        wDocs.title = (p.keyName || p.id) + " documentation";
      } else {
        wDocs.hidden = true;
        wizDocsUrl = null;
      }
    }
    if (wKeyStep) wKeyStep.hidden = !needs;
    if (wGetKey) {
      wGetKey.textContent = "Get " + (p && p.keyName ? p.keyName : "API") + " API Key";
      wGetKey.hidden = !needs || !(p && p.keyUrl);
    }
    // Bedrock auth mode: profile/SSO needs no key — hide the key inputs and
    // show the profile input instead.
    var bedrockProfileAuth = pid === "bedrock" && wBedrockAuth && wBedrockAuth.value === "aws-profile";
    if (wBedrockAuthRow) wBedrockAuthRow.hidden = (pid !== "bedrock");
    if (wBedrockProfileRow) wBedrockProfileRow.hidden = !bedrockProfileAuth;
    if (wKeyRow) wKeyRow.hidden = !!bedrockProfileAuth;
    if (wGetKeyRow) wGetKeyRow.hidden = !!bedrockProfileAuth;
    if (wFetchRow) wFetchRow.hidden = !(sm ? sm.fetchableProvider(pid) : (pid === "openai" || pid === "ollama"));
    if (wRegionRow) wRegionRow.hidden = (pid !== "bedrock");
    // Spec 2.1: truthful one-line blurb (echo is a demo, never "AI").
    if (wBlurb) wBlurb.textContent = (p && p.blurb) || "";
    updateWizardIntel();
  }

  function populateWizardModelSelect(models) {
    if (!wModelSelect || !wModel) return;
    wModelSelect.innerHTML = "";
    var current = wModel.value.trim();
    var seen = {};
    if (current && models.indexOf(current) < 0) {
      var cur = document.createElement("option");
      cur.value = current;
      cur.textContent = current + " (current)";
      wModelSelect.appendChild(cur);
      seen[current] = true;
    }
    models.forEach(function (id) {
      if (seen[id]) return;
      seen[id] = true;
      var o = document.createElement("option");
      o.value = id;
      o.textContent = id;
      wModelSelect.appendChild(o);
    });
    var manual = document.createElement("option");
    manual.value = WIZ_MANUAL;
    manual.textContent = "Type a model id manually\u2026";
    wModelSelect.appendChild(manual);
    if (wModelSelect.options) {
      wModelSelect.value = current && seen[current] ? current : models[0];
    } else {
      // minimal-DOM environments (headless tests): value sticks directly
      wModelSelect.value = current && seen[current] ? current : models[0];
    }
    wizLastSelect = wModelSelect.value;
    wModel.hidden = true;
    wModelSelect.hidden = false;
    updateWizardIntel();
  }

  function populateWizard(m) {
    var sm = setupMeta();
    if (wProvider && sm) {
      wProvider.innerHTML = "";
      sm.PROVIDERS.forEach(function (p) {
        var o = document.createElement("option");
        o.value = p.id;
        o.textContent = p.label;
        wProvider.appendChild(o);
      });
      sm.ensureSelectedOption(wProvider, (m && m.provider) || "echo");
    }
    if (wKey) wKey.value = "";
    if (wKeylabel) wKeylabel.value = "";
    if (wBedrockAuth) wBedrockAuth.value = "api-key";
    if (wBedrockProfile) wBedrockProfile.value = "";
    if (wModel) { wModel.value = ""; showWizardModelInput(); }
    if (wEndpoint) wEndpoint.value = "";
    if (wFetchNote) wFetchNote.textContent = "";
    if (wRegion && m && m.bedrockRegions) {
      wRegion.innerHTML = "";
      m.bedrockRegions.forEach(function (r) {
        var o = document.createElement("option");
        o.value = r;
        o.textContent = r;
        wRegion.appendChild(o);
      });
    }
    setWizardError("");
    updateWizardForProvider();
    // Spec 2.1: the prove-it step starts hidden on every fresh wizard open.
    if (wProveStep) wProveStep.hidden = true;
    if (wProveNote) wProveNote.textContent = "";
    if (wProve) wProve.disabled = false;
  }

  function setWizard(show, m) {
    if (!wizardEl) return;
    if (show && !wizardActive) {
      wizardActive = true;
      populateWizard(m);
      resetWizardSaveButton();
      wizardEl.hidden = false;
      // Spec 1.3: input bar stays in the DOM — only disabled, never hidden.
      setSetupCard(false);
      refreshInput();
    } else if (!show && wizardActive) {
      wizardActive = false;
      wizardEl.hidden = true;
      refreshInput();
    }
  }

  // scroll pinning (Copilot behavior) + jump-to-latest pill
  let pinned = true;
  const jumpPill = document.getElementById("jump-latest");
  function pinScroll() {
    if (pinned) { messages.scrollTop = messages.scrollHeight; }
    else if (jumpPill) { jumpPill.hidden = false; }
  }
  messages.addEventListener("scroll", function () {
    pinned = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 40;
    if (pinned && jumpPill) jumpPill.hidden = true;
  });
  if (jumpPill) jumpPill.addEventListener("click", function () {
    pinned = true;
    messages.scrollTop = messages.scrollHeight;
    jumpPill.hidden = true;
  });

  function getOrCreateStream(turnId, seedEv) {
    let st = streams.get(turnId);
    if (st) return st;
    const card = mk("div", "msg turn");
    if (seedEv) {
      const c = mk("div");
      c.innerHTML = chips({ phase: seedEv.phase, active_mode: seedEv.mode, persona: seedEv.persona });
      card.appendChild(c);
      const customMode = seedEv.mode && seedEv.mode.source && seedEv.mode.source !== "stage";
      setBead("mode", customMode ? "vdim" : "", customMode ? "mode: custom overlay" : "mode: stage default");
      setBead("persona", seedEv.persona ? "gdim" : "", seedEv.persona ? "persona: assumed" : "persona: none");
    }
    const thinkDetails = document.createElement("details");
    thinkDetails.className = "thinking";
    thinkDetails.open = true;
    const thinkSummary = mk("summary", "");
    thinkSummary.appendChild(mk("span", "bead working"));
    thinkSummary.appendChild(tnode(" \u25C8 Thinking "));
    thinkSummary.appendChild(mk("span", "thinking-status", "thinking\u2026"));
    const thinkBody = mk("div", "thinking-body");
    thinkDetails.appendChild(thinkSummary);
    thinkDetails.appendChild(thinkBody);
    const bodyEl = mk("div", "said md");
    const toolsEl = mk("div", "tools-live");
    const checksDetails = document.createElement("details");
    checksDetails.className = "checks";
    checksDetails.open = true;
    const checksSummary = mk("summary", "");
    checksSummary.appendChild(tnode("\u2B21 Harness checks "));
    const checksCount = mk("span", "checks-count");
    checksSummary.appendChild(checksCount);
    const checksRows = mk("div", "checks-rows");
    checksDetails.appendChild(checksSummary);
    checksDetails.appendChild(checksRows);
    card.appendChild(thinkDetails);
    card.appendChild(bodyEl);
    card.appendChild(toolsEl);
    card.appendChild(checksDetails);
    messages.appendChild(card);
    st = {
      turnId: turnId, card: card,
      thinkDetails: thinkDetails, thinkSummary: thinkSummary, thinkBody: thinkBody,
      bodyEl: bodyEl, toolsEl: toolsEl,
      checksDetails: checksDetails, checksRows: checksRows, checksCount: checksCount,
      thinkBuf: "", saidBuf: "", checks: [], toolRows: {},
      finalized: false, cancelled: false, saidPlain: false, thinkCut: false
    };
    streams.set(turnId, st);
    turnInFlight = true;
    setBead("turn", "working", "turn: streaming");
    refreshInput();
    pinScroll();
    return st;
  }

  function renderTurnStart(ev) {
    getOrCreateStream(ev.turn_id || "t?", ev);
  }

  function renderThinkingDelta(ev) {
    const st = getOrCreateStream(ev.turn_id || "t?", null);
    if (st.finalized || st.cancelled) return;
    let text = String(ev.text == null ? "" : ev.text);
    if (st.thinkBuf.length + text.length > THINK_CAP) {
      text = text.slice(0, THINK_CAP - st.thinkBuf.length);
      st.thinkCut = true;
    }
    st.thinkBuf += text;
    st.thinkBody.textContent = st.thinkBuf + (st.thinkCut ? "\u2026 [truncated]" : "");
    pinScroll();
  }

  function renderSaidDelta(ev) {
    const st = getOrCreateStream(ev.turn_id || "t?", null);
    if (st.finalized || st.cancelled) return;
    st.saidBuf += String(ev.text == null ? "" : ev.text);
    if (!st.saidPlain && st.saidBuf.length > SAID_MD_LIMIT) st.saidPlain = true;
    if (st.saidPlain) {
      st.bodyEl.textContent = st.saidBuf;
    } else {
      st.bodyEl.innerHTML = AwinoMarkdown(st.saidBuf) + '<span class="streaming-caret"></span>';
      AwinoMarkdown.wireCopyButtons(st.bodyEl);
    }
    pinScroll();
  }

  function renderToolProgress(ev) {
    const st = getOrCreateStream(ev.turn_id || "t?", null);
    if (st.finalized || st.cancelled) return;
    const key = ev.tool || "?";
    let row = st.toolRows[key];
    if (!row) {
      const elr = mk("div", "tool-row");
      const bead = mk("span", "bead working");
      const name = mk("span", "tname", esc(key));
      const sum = mk("span", "tsummary");
      const ms = mk("span", "tms");
      elr.appendChild(bead); elr.appendChild(name); elr.appendChild(sum); elr.appendChild(ms);
      st.toolsEl.appendChild(elr);
      row = st.toolRows[key] = { el: elr, bead: bead, sum: sum, ms: ms };
    }
    if (ev.phase === "start") {
      row.bead.className = "bead working";
      row.sum.textContent = ev.summary || "";
      row.ms.textContent = "";
    } else if (ev.phase === "done" || ev.phase === "error") {
      row.bead.className = "bead " + (ev.phase === "done" ? "done" : "alert");
      row.sum.textContent = ev.summary || "";
      row.ms.textContent = ev.ms != null ? ev.ms + " ms" : "";
    }
    pinScroll();
  }

  function checkRowHtml(c) {
    const v = c.verdict || "pass";
    return '<span class="verdict ' + esc(v) + '">' + esc(v) + "</span>" +
      '<span class="check-name">' + esc(c.check || "?") + "</span>" +
      '<span class="check-detail">' + esc(c.detail || "") + "</span>";
  }
  function updateChecksSummary(st) {
    const n = st.checks.length;
    const bad = st.checks.filter(function (c) { return c.verdict === "fail" || c.verdict === "warn"; }).length;
    st.checksCount.textContent = n ? " \u00B7 " + n + (bad ? " checked \u2014 " + bad + " need attention" : " passed") : "";
  }
  function renderHarnessCheck(ev) {
    const st = getOrCreateStream(ev.turn_id || "t?", null);
    if (st.finalized || st.cancelled) return;
    const c = { check: ev.check, verdict: ev.verdict, detail: ev.detail };
    st.checks.push(c);
    st.checksRows.appendChild(mk("div", "check-row", checkRowHtml(c)));
    updateChecksSummary(st);
    pinScroll();
  }

  // thinking trace behavior (§5)
  function collapseThinking(st, thinking) {
    st.thinkDetails.open = false;
    st.thinkSummary.innerHTML = ""; // clears children in real DOM and in the shim
    st.thinkSummary.appendChild(mk("span", thinking ? "bead done" : "bead"));
    st.thinkSummary.appendChild(tnode(" \u25C8 Thinking "));
    const lab = mk("span", "thinking-status");
    if (thinking) {
      st.thinkBody.textContent = thinking;
      lab.textContent = thinking.slice(0, 80) + (thinking.length > 80 ? "\u2026" : "") +
        " (" + thinking.length + " chars)";
    } else {
      st.thinkBody.textContent = "";
      lab.textContent = "not exposed by this provider";
    }
    st.thinkSummary.appendChild(lab);
  }

  function finalizeStream(st, r) {
    if (st.finalized) return;
    st.finalized = true;
    collapseThinking(st, st.thinkBuf || r.thinking || null);
    st.saidBuf = r.said != null ? String(r.said) : st.saidBuf;
    st.bodyEl.innerHTML = AwinoMarkdown(st.saidBuf);
    AwinoMarkdown.wireCopyButtons(st.bodyEl);
    if (Array.isArray(r.checks) && r.checks.length) {
      st.checksRows.innerHTML = "";
      st.checks = r.checks.map(function (c) { return { check: c.check, verdict: c.verdict, detail: c.detail }; });
      st.checks.forEach(function (c) { st.checksRows.appendChild(mk("div", "check-row", checkRowHtml(c))); });
    }
    const bad = st.checks.some(function (c) { return c.verdict === "fail" || c.verdict === "warn"; });
    st.checksDetails.open = bad; // stay expanded on fail/warn, else collapse
    updateChecksSummary(st);
    Object.keys(st.toolRows).forEach(function (k) {
      const row = st.toolRows[k];
      if (row.bead.className.indexOf("working") >= 0) row.bead.className = "bead done";
    });
    if (r.status === "awaiting_approval") {
      st.card.appendChild(mk("div", "", "<i>Awaiting your approval \u2014 see the approval card(s) below and the VS Code dialog.</i>"));
    }
    turnInFlight = false;
    setBead("turn", "", "turn: idle");
    refreshInput();
    pinScroll();
  }

  function renderTurnResult(ev) {
    const r = ev.result || {};
    const tid = ev.turn_id || r.turn_id || null;
    let st = tid ? streams.get(tid) : null;
    if (!st) {
      // fall back to the single in-flight stream (missed turn_start / untagged turn_result)
      streams.forEach(function (s) { if (!s.finalized && !s.cancelled && !st) st = s; });
    }
    if (st) {
      finalizeStream(st, r);
      return;
    }
    // legacy block path: same look, no streaming history
    const card = mk("div", "msg turn");
    const c = mk("div"); c.innerHTML = chips(r); card.appendChild(c);
    const thinkDetails = document.createElement("details");
    thinkDetails.className = "thinking";
    const thinkSummary = mk("summary", "");
    const thinkBody = mk("div", "thinking-body");
    thinkDetails.appendChild(thinkSummary);
    thinkDetails.appendChild(thinkBody);
    card.appendChild(thinkDetails);
    collapseThinking({ thinkDetails: thinkDetails, thinkSummary: thinkSummary, thinkBody: thinkBody },
      r.thinking || null);
    const body = mk("div", "said md", AwinoMarkdown(r.said || ""));
    card.appendChild(body);
    AwinoMarkdown.wireCopyButtons(card);
    const results = r.results || r.tool_results || [];
    if (Array.isArray(results) && results.length) {
      const tw = mk("div");
      tw.innerHTML = '<table class="tools"><tr><th>tool</th><th>result</th></tr>' +
        results.map(function (t) {
          const name = esc(t.tool || t.name || "?");
          const tbd = esc(JSON.stringify(t.result != null ? t.result : t).slice(0, 400));
          return "<tr><td>" + name + "</td><td><code>" + tbd + "</code></td></tr>";
        }).join("") + "</table>";
      card.appendChild(tw);
    }
    const checks = Array.isArray(r.checks) ? r.checks : [];
    if (checks.length) {
      const cd = document.createElement("details");
      cd.className = "checks";
      const bad = checks.some(function (x) { return x.verdict === "fail" || x.verdict === "warn"; });
      cd.open = bad;
      const cs = mk("summary", "");
      cs.appendChild(tnode("\u2B21 Harness checks "));
      cs.appendChild(mk("span", "checks-count",
        " \u00B7 " + checks.length + (bad ? " checked" : " passed")));
      const cr = mk("div", "checks-rows");
      checks.forEach(function (x) { cr.appendChild(mk("div", "check-row", checkRowHtml(x))); });
      cd.appendChild(cs); cd.appendChild(cr);
      card.appendChild(cd);
    }
    if (r.status === "awaiting_approval") {
      card.appendChild(mk("div", "", "<i>Awaiting your approval \u2014 see the approval card(s) below and the VS Code dialog.</i>"));
    }
    messages.appendChild(card);
    turnInFlight = false;
    refreshInput();
    pinScroll();
  }

  function renderApprovalRequested(ev) {
    const approvals = ev.approvals || [];
    setBead("approval", "alert", "approval: pending");
    approvals.forEach(function (a) {
      const d = addMsg("warn", "");
      let html = '<div class="approval"><h4>Approval requested: <code>' + esc(a.tool) + "</code></h4>";
      html += "<pre class=\"diff\">" + esc(JSON.stringify(a.args, null, 2)) + "</pre>";
      if (a.diff) {
        html += '<div>Diff preview:</div>' + AwinoMarkdown("```diff\n" + String(a.diff) + "\n```");
      }
      // approval-target visibility: resolved absolute targets + flag when
      // the command addresses outside the workspace (visibility only —
      // nothing here changes approve/deny semantics)
      var st = a.shell_targets;
      if (st) {
        if (st.outside_workspace) {
          html += '<div class="outside-banner">\u26a0\ufe0f This command addresses ' +
            'paths OUTSIDE the workspace. Review the resolved targets before deciding.</div>';
        }
        html += '<div class="targets"><div><b>Resolved targets</b> (cwd: ' +
          esc(st.effective_cwd || "") + ")</div>";
        (st.targets || []).forEach(function (t) {
          html += '<div class="trow"><span class="' + (t.in_workspace ? "in" : "out") + '">' +
            (t.in_workspace ? "\u2713" : "\u26a0\ufe0f") + "</span><code>" + esc(t.path) + "</code>" +
            (t.in_workspace ? "" : ' <span class="out">outside workspace</span>') + "</div>";
        });
        (st.unresolved || []).forEach(function (u) {
          html += '<div class="trow"><span class="in">?</span><code>' + esc(u.raw) + "</code>" +
            ' <span class="in">unresolved: ' + esc(u.reason || "") + "</span></div>";
        });
        html += "</div>";
      }
      html += '<div class="btnrow">' +
        '<button data-act="approve">Approve</button>' +
        '<button data-act="deny" class="secondary">Deny</button></div></div>';
      d.innerHTML = html;
      AwinoMarkdown.wireCopyButtons(d);
      d.querySelectorAll("button").forEach(function (b) {
        b.addEventListener("click", function () {
          vscode.postMessage({ type: "approve", id: a.id, decision: b.getAttribute("data-act") });
          b.disabled = true;
          const spin = mk("span", "bead working");
          spin.title = "waiting for sidecar";
          b.parentNode.appendChild(spin);
          setBead("approval", "", "approval: none");
        });
      });
    });
  }

  function renderCompaction(ev) {
    const d = addMsg("warn", "");
    const html =
      '<div class="approval"><h4>Compaction proposed</h4>' +
      "<div>Projected tokens: <b>" + esc(ev.projected_tokens) + "</b> / window " + esc(ev.window) + "</div>" +
      "<div>Estimated savings: " + esc(ev.estimated_savings) + "</div>" +
      "<div>Pinned (never reduced): <code>" + esc(JSON.stringify(ev.pinned)) + "</code></div>" +
      "<div>" + esc(ev.note || "") + "</div>" +
      '<div class="btnrow"><button data-act="approve">Approve compaction</button>' +
      '<button data-act="deny" class="secondary">Deny</button></div></div>';
    d.innerHTML = html;
    d.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        vscode.postMessage({ type: "approve", id: ev.proposal_id, decision: b.getAttribute("data-act") });
        b.disabled = true;
      });
    });
  }

  function renderEvent(ev) {
    switch (ev.event) {
      case "ready": {
        const b = ev.binding || {};
        setStatusText(
          "connected · " + (b.provider || ev.provider) + " · env " + (b.environment || "(global)") +
          " · model " + (b.model || ev.model || "?"));
        setBead("link", "on", "link: connected");
        addMsg("", "<i>Sidecar ready — project <b>" + esc(ev.project) + "</b>, provider <b>" +
          esc(b.provider || ev.provider) + "</b>. MCP: " +
          esc(JSON.stringify((ev.mcp || []).map(function (m) { return m.name + ":" + (m.ok ? "ok" : "error"); }))) + "</i>");
        break;
      }
      case "turn_start":
        renderTurnStart(ev);
        break;
      case "thinking_delta":
        renderThinkingDelta(ev);
        break;
      case "said_delta":
        renderSaidDelta(ev);
        break;
      case "tool_progress":
        renderToolProgress(ev);
        break;
      case "harness_check":
        renderHarnessCheck(ev);
        break;
      case "turn_result":
        renderTurnResult(ev);
        break;
      case "approval_requested":
        renderApprovalRequested(ev);
        break;
      case "compaction_proposed":
        renderCompaction(ev);
        break;
      case "command_result":
        addMsg("", "<i>command <code>" + esc(ev.name) + "</code>: " +
          (ev.ok ? "ok" : "<b>failed</b>") + " — <code>" +
          esc(JSON.stringify(ev.result).slice(0, 500)) + "</code></i>");
        break;
      case "cancel_ack": {
        let frozen = false;
        streams.forEach(function (st) {
          if (!st.finalized && !st.cancelled) {
            st.cancelled = true;
            st.thinkDetails.open = false;
            Object.keys(st.toolRows).forEach(function (k) {
              st.toolRows[k].bead.className = "bead";
            });
            st.card.appendChild(mk("div", "cancelled-note",
              '<i class="dm">turn cancelled \u2014 effects already executed remain in the journal</i>'));
            frozen = true;
          }
        });
        if (frozen) pinScroll();
        setBead("turn", "", "turn: idle");
        addMsg("warn", "<i>Turn cancelled: " + esc(ev.note) + "</i>");
        turnInFlight = false;
        refreshInput();
        break;
      }
      case "warning":
        addMsg("warn", esc(ev.message));
        break;
      case "error":
        addMsg("error", "<b>Error:</b> " + esc(ev.message));
        turnInFlight = false;
        refreshInput();
        break;
      case "bye":
        setStatusText("disconnected");
        setBead("link", "", "link: disconnected");
        break;
      default:
        addMsg("", "<i>[" + esc(ev.event) + "]</i> <code>" + esc(JSON.stringify(ev).slice(0, 300)) + "</code>");
    }
  }

  function refreshInput() {
    // Spec 1.3: the input bar is NEVER hidden — states only disable it or
    // change the placeholder. Wizard showing → disabled; not connected →
    // disabled; turn in flight → disabled.
    var blocked = turnInFlight || wizardActive || !chatConnected;
    sendBtn.disabled = blocked;
    input.disabled = blocked;
    if (wizardActive) {
      input.placeholder = "Finish setup above to start chatting";
    } else if (!chatConnected) {
      input.placeholder = "Awino is not connected — check the status bar";
    } else {
      input.placeholder = "Message Awino…";
    }
  }

  function setConnected(connected) {
    chatConnected = !!connected;
    if (connDot) {
      connDot.classList.toggle("on", chatConnected);
      connDot.classList.toggle("off", !chatConnected);
      connDot.title = chatConnected ? "Connected" : "Not connected";
    }
    refreshInput();
  }
  // Connection state for input gating (Spec 1.3). Set from state messages.
  var chatConnected = false;

  function send() {
    const text = input.value.trim();
    // Never send while disconnected — the input is disabled, but guard here
    // too in case of a race between the disable and a keypress.
    if (!text || turnInFlight || !chatConnected) return;
    addMsg("user", '<div class="said">' + esc(text) + "</div>");
    input.value = "";
    turnInFlight = true;
    refreshInput();
    vscode.postMessage({ type: "send", text: text });
  }

  sendBtn.addEventListener("click", send);
  stopBtn.addEventListener("click", function () {
    vscode.postMessage({ type: "stop" });
  });
  // Header gear + setup-card button: both open Models & Providers, which is
  // where API keys live (SecretStorage) since keys are not in Settings.
  // The extension host handles "models" with no session required, so this
  // works even when nothing is connected.
  const openModelsPanel = function () { vscode.postMessage({ type: "models" }); };
  if (modelsBtn) modelsBtn.addEventListener("click", openModelsPanel);
  if (setupBtn) setupBtn.addEventListener("click", openModelsPanel);
  // Provider status pill: same destination as the gear — the click target
  // a new user actually finds (Kilo pattern).
  if (providerPill) providerPill.addEventListener("click", openModelsPanel);

  // ---------- Spec 1.4: mode selector + new-mission button ----------
  function setModeOptions(modes, activeId) {
    if (!modeSelect) return;
    if (!modes || !modes.length) {
      modeSelect.hidden = true;
      return;
    }
    modeSelect.hidden = false;
    var cur = modeSelect.value;
    modeSelect.innerHTML = "";
    modes.forEach(function (md) {
      var o = document.createElement("option");
      o.value = md.id;
      o.textContent = md.label || md.id;
      if (md.id === activeId) o.selected = true;
      modeSelect.appendChild(o);
    });
    // Set the value explicitly: preserves the user's pending selection if
    // still valid, otherwise reflects the true active mode. (Real DOM syncs
    // select.value from the selected option; be explicit for safety.)
    var target = activeId || "";
    if (cur) {
      for (var i = 0; i < modes.length; i++) {
        if (modes[i].id === cur) { target = cur; break; }
      }
    }
    try { modeSelect.value = target; } catch (e) { /* non-DOM stub */ }
  }
  if (modeSelect) modeSelect.addEventListener("change", function () {
    var id = modeSelect.value;
    if (id) vscode.postMessage({ type: "invokeModeSelect", mode: id });
    // Reset to the active mode until the host confirms the switch.
    // The next state/modesList message re-renders the true active mode.
  });
  if (newMissionBtn) newMissionBtn.addEventListener("click", function () {
    vscode.postMessage({ type: "newMission" });
  });

  // ---------- wizard events ----------
  if (wProvider) wProvider.addEventListener("change", updateWizardForProvider);
  if (wBedrockAuth) wBedrockAuth.addEventListener("change", updateWizardForProvider);
  if (wDocs) wDocs.addEventListener("click", function (e) {
    if (e && e.preventDefault) e.preventDefault();
    if (wizDocsUrl) vscode.postMessage({ type: "openExternal", url: wizDocsUrl });
  });
  if (wGetKey) wGetKey.addEventListener("click", function () {
    var sm = setupMeta();
    var p = sm ? sm.providerById(wProvider ? wProvider.value : "") : null;
    if (p && p.keyUrl) vscode.postMessage({ type: "openExternal", url: p.keyUrl });
  });
  if (wModel) {
    wModel.addEventListener("change", updateWizardIntel);
    wModel.addEventListener("input", updateWizardIntel);
  }
  if (wModelSelect) wModelSelect.addEventListener("change", function () {
    if (wModelSelect.value === WIZ_MANUAL) {
      if (wizLastSelect && wizLastSelect !== WIZ_MANUAL && wModel) wModel.value = wizLastSelect;
      showWizardModelInput();
      if (wModel) wModel.focus();
    } else {
      wizLastSelect = wModelSelect.value;
    }
    updateWizardIntel();
  });
  if (wFetch) wFetch.addEventListener("click", function () {
    if (!wProvider || !wEndpoint || !wKey || !wFetchNote) return;
    var provider = wProvider.value;
    var endpoint = wEndpoint.value.trim();
    var key = wKey.value;
    if (provider === "openai" && !endpoint && !key) {
      wFetchNote.textContent = "Enter an endpoint and API key first, then fetch.";
      return;
    }
    setWizardError("");
    wFetchNote.textContent = "fetching\u2026";
    vscode.postMessage({ type: "wizardFetch", provider: provider, endpoint: endpoint, key: key });
  });
  if (wDone) wDone.addEventListener("click", function () {
    if (!wProvider) return;
    var sm = setupMeta();
    var provider = wProvider.value;
    var needs = sm ? sm.needsKey(provider)
      : (provider === "openai" || provider === "openai-compatible" || provider === "anthropic" || provider === "bedrock");
    var bedrockProfileAuth = provider === "bedrock" && wBedrockAuth && wBedrockAuth.value === "aws-profile";
    if (bedrockProfileAuth) {
      // SigV4 from the AWS chain: no key required, and the extension must
      // not demand one.
      needs = false;
      var profName = wBedrockProfile ? wBedrockProfile.value.trim() : "";
      if (!profName) {
        setWizardError("Enter the AWS profile name (from ~/.aws/config) \u2014 or pick the API key option above.");
        if (wBedrockProfile) wBedrockProfile.focus();
        return;
      }
    }
    if (needs && wKey && !wKey.value) {
      // Roo-style fail-closed inline validation: no silent no-op.
      setWizardError("Enter an API key for this provider \u2014 or choose echo or ollama for the keyless path.");
      wKey.focus();
      return;
    }
    setWizardError("");
    setWizardSaving(true);
    vscode.postMessage({
      type: "wizardSave",
      provider: provider,
      endpoint: wEndpoint ? wEndpoint.value.trim() : "",
      model: currentWizardModel(),
      key: wKey ? wKey.value : "",
      keyLabel: wKeylabel ? wKeylabel.value.trim() : "",
      bedrockRegion: wRegion ? wRegion.value : "",
      bedrockAuthMode: bedrockProfileAuth ? "aws-profile" : "api-key",
      bedrockAwsProfile: bedrockProfileAuth ? wBedrockProfile.value.trim() : "",
    });
  });
  if (wSkip) wSkip.addEventListener("click", function () {
    vscode.postMessage({ type: "wizardDismiss" });
  });
  // Spec 2.1 Step 3: send the fixed prove-it message through the live sidecar.
  if (wProve) wProve.addEventListener("click", function () {
    wProve.disabled = true;
    if (wProveNote) wProveNote.textContent = "Sending…";
    setWizardError("");
    vscode.postMessage({ type: "wizardProve" });
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  window.addEventListener("message", function (e) {
    const m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "event" && m.payload) {
      renderEvent(m.payload);
    } else if (m.type === "wizardProveReady") {
      // Spec 2.1 Step 3: settings saved and sidecar connected — show the
      // prove-it step. The earlier steps are done; only the test message
      // remains.
      resetWizardSaveButton();
      if (wProveStep) wProveStep.hidden = false;
      if (wProveNote) wProveNote.textContent = "";
      if (wProve) wProve.disabled = false;
      setWizardError("");
    } else if (m.type === "wizardProved") {
      // The provider answered — onboarding is complete. The host already
      // marked onboarded and re-pushed state; this just confirms in place.
      if (wProveNote) wProveNote.textContent = "It answered — setup complete.";
      if (wProve) wProve.disabled = true;
    } else if (m.type === "wizardProveFailed") {
      if (wProveNote) wProveNote.textContent = "";
      if (wProve) wProve.disabled = false;
      setWizardError("The test message failed: " + (m.error || "unknown error") +
        " — check the key, endpoint, and model, then try again.");
    } else if (m.type === "wizardSaveFailed") {
      // The key could not be stored (e.g. no working system keyring):
      // surface it inline and re-enable Save & Connect. The button was
      // disabled on click so a second attempt is always possible.
      resetWizardSaveButton();
      setWizardError("Could not save the API key: " + (m.error || "unknown error"));
    } else if (m.type === "wizardModels") {
      // Model discovery results for the onboarding wizard (host-side fetch).
      if (m.ok && m.models && m.models.length) {
        populateWizardModelSelect(m.models);
        if (wFetchNote) wFetchNote.textContent =
          "Found " + m.models.length + " models \u2014 pick one, or type manually.";
      } else {
        showWizardModelInput();
        if (wFetchNote) wFetchNote.textContent =
          "Couldn't fetch models: " + (m.error || "unknown error") + " \u2014 type a model id manually.";
      }
    } else if (m.type === "bindingChanged") {
      // Spec 4.3: single publish path — binding updates arrive here.
      // Re-render the pill/dot from the authoritative binding.
      // Residual fix: a null binding means disconnected (fatal crash path
      // sends bindingChanged before/without a state message) — update the
      // connection state so the input disables immediately instead of
      // staying enabled while the header says "not connected".
      if (!m.binding) {
        setConnected(false);
      }
      setProviderPill({
        connected: chatConnected,
        ready: { binding: m.binding || {} },
        settingsDirty: m.settingsDirty === true,
      });
      if (connDot && m.binding) {
        connDot.classList.toggle("on", chatConnected);
        connDot.classList.toggle("off", !chatConnected);
      }
    } else if (m.type === "modesList") {
      // Spec 1.4: mode selector options from the live sidecar.
      setModeOptions(m.modes || [], m.activeMode || "");
    } else if (m.type === "state") {
      // Provider pill always reflects the binding; the wizard supersedes the
      // setup card while it owns the first-run setup flow.
      setConnected(m.connected === true);
      setProviderPill(m);
      setWizard(m.showWizard === true, m);
      setModeOptions(m.modes || [], m.activeMode || "");
      // 0.4.1: persistent mission header from the live sidecar status.
      setMissionHeader(m.status);
      // Missing API key for the active provider -> setup card at the top of
      // the chat, regardless of the connected flag (a keyless provider may
      // never reach "connected").
      setSetupCard(m.keyMissing === true && !wizardActive, m.provider);
      if (m.connected === false) {
        if (m.connectError) {
          // Sidecar failed to spawn/start: show the OS error + interpreter
          // tried + fix hint in the statusline and banner, not the bare
          // "not connected".
          setStatusText("not connected — sidecar failed to start");
          setBead("link", "alert", "link: " + String(m.connectError).slice(0, 200));
          banner.innerHTML = "<b>Sidecar failed to start.</b> " + esc(String(m.connectError));
          banner.classList.add("show");
        } else {
          setStatusText("not connected");
          setBead("link", "", "link: not connected");
        }
      } else if (m.ready) {
        renderEvent(m.ready);
      }
      // mission-first: with no mission, show the interview banner, never a blank chat
      const st = m.status || {};
      if (m.connected && st.mission == null) {
        banner.innerHTML = "<b>No active mission.</b> Say what you want to build and the harness opens its discovery interview. " +
          "Or run <b>Awino: New Mission</b> / <b>New Mission from Seed</b> from the command palette.";
        banner.classList.add("show");
      } else if (!(m.connected === false && m.connectError)) {
        banner.classList.remove("show");
      }
      // Spec 1.3: scroll to bottom after state rehydration (postChatState on
      // view resolve) so the reloaded chat shows the latest messages.
      if (messages) messages.scrollTop = messages.scrollHeight;
    } else if (m.type === "sessionResume") {
      // 0.4.1: read-only session-focus summary from the event log,
      // posted on (re)connect and on demand via the command palette.
      renderSessionResume(m.summary);
    }
  });

  refreshInput();
  // Ready handshake: the message listener above is live, so the host can
  // now (re)deliver the retained transcript + fresh state. Fires on every
  // (re)load, i.e. exactly when the DOM is empty and needs filling.
  vscode.postMessage({ type: "chatReady" });
})();
}
