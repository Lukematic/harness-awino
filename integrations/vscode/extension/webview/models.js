/* models.js — Models & Providers settings panel (plain JS, no build).
 * Reads every awino.* setting (provider/endpoint/model/timeout/mcpServers are
 * surfaced or documented); keys go to SecretStorage via the extension host.
 */
(function () {
  const vscode = acquireVsCodeApi();
  const $ = function (id) { return document.getElementById(id); };
  const status = $("status");

  function setKeyState(name, isSet, label) {
    // "key set (Work)" when the user named the key, plain "key set" when
    // they didn't, "not set" when nothing is in SecretStorage.
    var el = $(name + "KeyState");
    el.textContent = isSet ? (label ? "key set (" + label + ")" : "key set") : "not set";
    el.className = isSet ? "key-stored" : "key-unset";
  }

  function render(cfg, binding, environments, keys, bedrockRegions, keyLabels) {
    var labels = keyLabels || {};
    $("provider").value = cfg.provider || "echo";
    $("endpoint").value = cfg.endpoint || "";
    $("model").value = cfg.model || "";
    $("timeout").value = cfg.timeout || 180;
    setKeyState("openai", keys.openai, labels.openai);
    setKeyState("anthropic", keys.anthropic, labels.anthropic);
    setKeyState("bedrock", keys.bedrock, labels.bedrock);
    $("openaiKeyLabel").value = labels.openai || "";
    $("anthropicKeyLabel").value = labels.anthropic || "";
    $("bedrockKeyLabel").value = labels.bedrock || "";
    updateFetchUi();

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

  // ---------- model discovery ----------
  // "Fetch models" is offered for openai-compatible (GET {endpoint}/models)
  // and ollama (GET {endpoint}/api/tags). Bedrock keeps manual entry — its
  // model list isn't listable the same way. A failed/empty fetch never
  // blocks saving: the manual text input stays, with a plain-language note.
  var MANUAL = "__awino_manual__";
  var lastSelectValue = "";

  function fetchableProvider(p) {
    return p === "openai" || p === "ollama";
  }

  function updateFetchUi() {
    var show = fetchableProvider($("provider").value);
    $("fetchRow").hidden = !show;
    if (!show) {
      showModelInput();
      setFetchNote("", "");
    }
  }

  function setFetchNote(text, cls) {
    var n = $("fetchNote");
    n.textContent = text;
    n.className = "note" + (cls ? " " + cls : "");
  }

  function showModelInput() {
    $("model").hidden = false;
    $("modelSelect").hidden = true;
  }

  function currentModelValue() {
    var sel = $("modelSelect");
    return sel.hidden ? $("model").value.trim() : sel.value;
  }

  function populateModelSelect(models) {
    var sel = $("modelSelect");
    sel.innerHTML = "";
    var current = $("model").value.trim();
    var seen = {};
    if (current && models.indexOf(current) < 0) {
      // keep the user's current value selectable instead of silently dropping it
      var cur = document.createElement("option");
      cur.value = current;
      cur.textContent = current + " (current)";
      sel.appendChild(cur);
      seen[current] = true;
    }
    models.forEach(function (id) {
      if (seen[id]) {
        return;
      }
      seen[id] = true;
      var o = document.createElement("option");
      o.value = id;
      o.textContent = id;
      sel.appendChild(o);
    });
    var manual = document.createElement("option");
    manual.value = MANUAL;
    manual.textContent = "Type a model id manually…";
    sel.appendChild(manual);
    sel.value = current && seen[current] ? current : models[0];
    lastSelectValue = sel.value;
    $("model").hidden = true;
    sel.hidden = false;
  }

  $("provider").addEventListener("change", updateFetchUi);

  $("modelSelect").addEventListener("change", function () {
    var sel = $("modelSelect");
    if (sel.value === MANUAL) {
      if (lastSelectValue) {
        $("model").value = lastSelectValue;
      }
      showModelInput();
      $("model").focus();
    } else {
      lastSelectValue = sel.value;
    }
  });

  $("fetchModels").addEventListener("click", function () {
    var provider = $("provider").value;
    var endpoint = $("endpoint").value.trim();
    var key = provider === "openai" ? $("openaiKey").value : "";
    if (provider === "openai" && !endpoint && !key) {
      setFetchNote("Enter an endpoint and API key first, then fetch.", "warn");
      return;
    }
    setFetchNote("fetching…", "");
    vscode.postMessage({ type: "fetchModels", provider: provider, endpoint: endpoint, key: key });
  });

  $("save").addEventListener("click", function () {
    vscode.postMessage({
      type: "save",
      provider: $("provider").value,
      endpoint: $("endpoint").value.trim(),
      model: currentModelValue(),
      bedrockRegion: $("bedrockRegion").value,
      timeout: Number($("timeout").value) || 180,
      openaiKey: $("openaiKey").value,
      anthropicKey: $("anthropicKey").value,
      bedrockKey: $("bedrockKey").value,
      openaiKeyLabel: $("openaiKeyLabel").value.trim(),
      anthropicKeyLabel: $("anthropicKeyLabel").value.trim(),
      bedrockKeyLabel: $("bedrockKeyLabel").value.trim(),
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
      model: currentModelValue(), timeout: Number($("timeout").value) || 180,
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
        m.bedrockRegions || [], m.keyLabels || {});
    } else if (m.type === "modelsFetched") {
      if (m.ok && m.models && m.models.length) {
        populateModelSelect(m.models);
        setFetchNote("Found " + m.models.length + " models — pick one, or type manually.", "");
      } else {
        showModelInput();
        setFetchNote("Couldn't fetch models: " + (m.error || "unknown error") + " — type a model id manually.", "warn");
      }
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
