/* models.js — Models & Providers settings panel (plain JS, no build).
 * Reads every awino.* setting (provider/endpoint/model/timeout/mcpServers are
 * surfaced or documented); keys go to SecretStorage via the extension host.
 */
(function () {
  const vscode = acquireVsCodeApi();
  const $ = function (id) { return document.getElementById(id); };
  const status = $("status");

  function render(cfg, binding, environments, keys, bedrockRegions) {
    $("provider").value = cfg.provider || "echo";
    $("endpoint").value = cfg.endpoint || "";
    $("model").value = cfg.model || "";
    $("timeout").value = cfg.timeout || 180;
    $("openaiKeyState").textContent = keys.openai ? "(stored)" : "(not set)";
    $("anthropicKeyState").textContent = keys.anthropic ? "(stored)" : "(not set)";
    $("bedrockKeyState").textContent = keys.bedrock ? "(stored)" : "(not set)";

    var regionSel = $("bedrockRegion");
    regionSel.innerHTML = "";
    (bedrockRegions || []).forEach(function (r) {
      var o = document.createElement("option");
      o.value = r; o.textContent = r;
      if (r === cfg.bedrockRegion) o.selected = true;
      regionSel.appendChild(o);
    });

    const b = binding || {};
    status.innerHTML =
      "<b>Current binding</b><br>" +
      "provider: <b>" + esc(b.provider || "?") + "</b> · model: <b>" + esc(b.model || "?") + "</b><br>" +
      "environment: <b>" + esc(b.environment || "(global settings)") + "</b> · source: " + esc(b.source || "?") + "<br>" +
      "key: <b>" + esc(b.key || "not-required") + "</b> <span class='note'>(status only — material lives in secret storage)</span>";

    const envSel = $("env");
    envSel.innerHTML = "";
    const envs = environments && environments.length ? environments : ["(global settings)"];
    envs.forEach(function (e) {
      const o = document.createElement("option");
      o.value = e; o.textContent = e;
      if (e === b.environment) o.selected = true;
      envSel.appendChild(o);
    });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  $("save").addEventListener("click", function () {
    vscode.postMessage({
      type: "save",
      provider: $("provider").value,
      endpoint: $("endpoint").value.trim(),
      model: $("model").value.trim(),
      bedrockRegion: $("bedrockRegion").value,
      timeout: Number($("timeout").value) || 180,
      openaiKey: $("openaiKey").value,
      anthropicKey: $("anthropicKey").value,
      bedrockKey: $("bedrockKey").value,
      clearKeys: false,
    });
    $("openaiKey").value = "";
    $("anthropicKey").value = "";
    $("bedrockKey").value = "";
    status.innerHTML = "<i>reconnecting…</i>";
  });

  $("clearKeys").addEventListener("click", function () {
    vscode.postMessage({ type: "save",
      provider: $("provider").value, endpoint: $("endpoint").value.trim(),
      model: $("model").value.trim(), timeout: Number($("timeout").value) || 180,
      clearKeys: true });
  });

  $("switchEnv").addEventListener("click", function () {
    const e = $("env").value;
    vscode.postMessage({ type: "switchEnv", environment: e });
    $("envResult").textContent = "switching…";
  });

  window.addEventListener("message", function (e) {
    const m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "state") {
      render(m.config || {}, m.binding || null, m.environments || [],
        { openai: !!m.openaiKeySet, anthropic: !!m.anthropicKeySet, bedrock: !!m.bedrockKeySet },
        m.bedrockRegions || []);
    } else if (m.type === "reconnected") {
      $("envResult").textContent = m.ok ? "reconnected" : "reconnect failed — see output channel";
      vscode.postMessage({ type: "init" }); // refresh state
    } else if (m.type === "envSwitched") {
      $("envResult").textContent = m.ok
        ? "environment switched"
        : "switch refused: " + JSON.stringify(m.result).slice(0, 300);
      vscode.postMessage({ type: "init" });
    }
  });

  vscode.postMessage({ type: "init" });
})();
