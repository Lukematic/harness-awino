/* chat.js — A.W.I.N.O. mission chat renderer (plain JS, no build).
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
  let turnInFlight = false;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function addMsg(cls, html) {
    const d = document.createElement("div");
    d.className = "msg " + cls;
    d.innerHTML = html;
    messages.appendChild(d);
    messages.scrollTop = messages.scrollHeight;
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

  function renderTurnResult(ev) {
    const r = ev.result || {};
    let html = chips(r);
    if (r.said) html += '<div class="said">' + esc(r.said) + "</div>";
    const results = r.results || r.tool_results || [];
    if (Array.isArray(results) && results.length) {
      html += '<table class="tools"><tr><th>tool</th><th>result</th></tr>' +
        results.map(function (t) {
          const name = esc(t.tool || t.name || "?");
          const body = esc(JSON.stringify(t.result != null ? t.result : t).slice(0, 400));
          return "<tr><td>" + name + "</td><td><code>" + body + "</code></td></tr>";
        }).join("") + "</table>";
    }
    if (r.status === "awaiting_approval") {
      html += "<div><i>Awaiting your approval — see the approval card(s) below and the VS Code dialog.</i></div>";
    }
    addMsg("turn", html);
    turnInFlight = false;
    refreshInput();
  }

  function renderApprovalRequested(ev) {
    const approvals = ev.approvals || [];
    approvals.forEach(function (a) {
      const d = addMsg("warn", "");
      let html = '<div class="approval"><h4>Approval requested: <code>' + esc(a.tool) + "</code></h4>";
      html += "<pre class=\"diff\">" + esc(JSON.stringify(a.args, null, 2)) + "</pre>";
      if (a.diff) {
        html += '<div>Diff preview:</div><pre class="diff">' + esc(a.diff) + "</pre>";
      }
      html += '<div class="btnrow">' +
        '<button data-act="approve">Approve</button>' +
        '<button data-act="deny" class="secondary">Deny</button></div></div>';
      d.innerHTML = html;
      d.querySelectorAll("button").forEach(function (b) {
        b.addEventListener("click", function () {
          vscode.postMessage({ type: "approve", id: a.id, decision: b.getAttribute("data-act") });
          b.disabled = true;
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
        statusline.textContent =
          "connected · " + (b.provider || ev.provider) + " · env " + (b.environment || "(global)") +
          " · model " + (b.model || ev.model || "?");
        addMsg("", "<i>Sidecar ready — project <b>" + esc(ev.project) + "</b>, provider <b>" +
          esc(b.provider || ev.provider) + "</b>. MCP: " +
          esc(JSON.stringify((ev.mcp || []).map(function (m) { return m.name + ":" + (m.ok ? "ok" : "error"); }))) + "</i>");
        break;
      }
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
      case "cancel_ack":
        addMsg("warn", "<i>Turn cancelled: " + esc(ev.note) + "</i>");
        turnInFlight = false;
        refreshInput();
        break;
      case "warning":
        addMsg("warn", esc(ev.message));
        break;
      case "error":
        addMsg("error", "<b>Error:</b> " + esc(ev.message));
        turnInFlight = false;
        refreshInput();
        break;
      case "bye":
        statusline.textContent = "disconnected";
        break;
      default:
        addMsg("", "<i>[" + esc(ev.event) + "]</i> <code>" + esc(JSON.stringify(ev).slice(0, 300)) + "</code>");
    }
  }

  function refreshInput() {
    sendBtn.disabled = turnInFlight;
    input.disabled = turnInFlight;
  }

  function send() {
    const text = input.value.trim();
    if (!text || turnInFlight) return;
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
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  window.addEventListener("message", function (e) {
    const m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "event" && m.payload) {
      renderEvent(m.payload);
    } else if (m.type === "state") {
      if (m.connected === false) {
        statusline.textContent = "not connected";
      } else if (m.ready) {
        renderEvent(m.ready);
      }
      // mission-first: with no mission, show the interview banner, never a blank chat
      const st = m.status || {};
      if (m.connected && st.mission == null) {
        banner.innerHTML = "<b>No active mission.</b> Say what you want to build and the harness opens its discovery interview. " +
          "Or run <b>A.W.I.N.O.: New Mission</b> / <b>New Mission from Seed</b> from the command palette.";
        banner.classList.add("show");
      } else {
        banner.classList.remove("show");
      }
    }
  });

  refreshInput();
})();
}
