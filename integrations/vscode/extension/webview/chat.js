/* chat.js — A.W.I.N.O. mission chat renderer (plain JS, no build).
 * Renders every §4 sidecar event; forwards user input via acquireVsCodeApi().
 * The webview never constructs turns or calls tools — it only displays
 * events and sends the §4 command verbs.
 */
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
