/* models.js — Models & Providers settings panel (plain JS, no build).
 * Reads every awino.* setting (provider/endpoint/model/timeout/mcpServers are
 * surfaced or documented); keys go to SecretStorage via the extension host.
 */
(function () {
  const vscode = acquireVsCodeApi();
  const $ = function (id) { return document.getElementById(id); };
  const status = $("status");

  // Shared provider catalogue / model-intel (webview/setup-shared.js, loaded
  // before this file). Lazily accessed so a failed load degrades instead of
  // killing the panel.
  function setupMeta() {
    return (typeof AwinoSetup !== "undefined" && AwinoSetup) || null;
  }

  function openExternal(url) {
    if (url) vscode.postMessage({ type: "openExternal", url: url });
  }

  function setKeyState(name, isSet, label) {
    // "key set (Work)" when the user named the key, plain "key set" when
    // they didn't, "not set" when nothing is in SecretStorage.
    var el = $(name + "KeyState");
    el.textContent = isSet ? (label ? "key set (" + label + ")" : "key set") : "not set";
    el.className = isSet ? "key-stored" : "key-unset";
  }

  function render(cfg, binding, environments, keys, bedrockRegions, keyLabels) {
    var labels = keyLabels || {};
    // (d) MUST-FIX: never render the provider dropdown blank. The stored
    // provider can be a legacy id no <option> carries (0.3.0 left "scripted"
    // behind) — surface the real binding as a labeled option instead.
    var sm = setupMeta();
    if (sm) {
      sm.ensureSelectedOption($("provider"), cfg.provider || "echo");
    } else {
      $("provider").value = cfg.provider || "echo";
    }
    updateProviderDocs();
    $("endpoint").value = cfg.endpoint || "";
    $("model").value = cfg.model || "";
    updateModelIntel();
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
    const hasBinding = !!(binding && binding.provider);
    // Spec 4.4: when not connected, the card header becomes "No active
    // connection" and the body shows configured (not yet applied) values.
    // This fixes the stale-text residual: a crash while the panel is open
    // now re-renders to the disconnected state instead of keeping the
    // last live binding.
    status.innerHTML = hasBinding
      ? "<b>Current binding</b><br>" +
        "provider: <b>" + esc(b.provider || "?") + "</b> · model: <b>" + esc(b.model || "?") + "</b><br>" +
        "environment: <b>" + esc(b.environment || "(global settings)") + "</b> · source: " + esc(b.source || "?") + "<br>" +
        "key: <b>" + esc(b.key || "not-required") + "</b> <span class='note'>(status only — material lives in secret storage)</span>"
      : "<b>No active connection</b><br>" +
        "<span class='note'>Configured values (not yet applied):</span><br>" +
        "provider: <b>" + esc(cfg.provider || "echo") + "</b> · model: <b>" + esc(cfg.model || "(default)") + "</b> " +
        "<span class='note'>(configured, not active)</span><br>" +
        "<button id=\"reconnectApply\" class=\"btn\">Reconnect to apply</button>";

    const envSel = $("env");
    envSel.innerHTML = "";
    const envs = environments && environments.length ? environments : ["(global settings)"];
    envs.forEach(function (e) {
      const o = document.createElement("option");
      o.value = e; o.textContent = e;
      if (e === b.environment) o.selected = true;
      envSel.appendChild(o);
    });

    // Wire the "Reconnect to apply" button shown in the disconnected state.
    var raBtn = $("reconnectApply");
    if (raBtn) {
      raBtn.addEventListener("click", function () {
        vscode.postMessage({ type: "reconnect" });
        status.innerHTML = "<i>reconnecting…</i>";
      });
    }
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
    var sm = setupMeta();
    var show = sm ? sm.fetchableProvider($("provider").value) : fetchableProvider($("provider").value);
    $("fetchRow").hidden = !show;
    if (!show) {
      showModelInput();
      setFetchNote("", "");
    }
  }

  // (c) "Provider Docs" link next to the provider dropdown — follows the
  // selected provider; hidden when the provider needs no docs (echo).
  var currentDocsUrl = null;
  function updateProviderDocs() {
    var link = $("providerDocs");
    if (!link) return;
    var sm = setupMeta();
    var p = sm ? sm.providerById($("provider").value) : null;
    if (p && p.docsUrl) {
      link.hidden = false;
      currentDocsUrl = p.docsUrl;
      link.title = (p.keyName || p.id) + " documentation";
    } else {
      link.hidden = true;
      currentDocsUrl = null;
    }
  }

  // (e) Model intelligence line under the picker: context window + $/M
  // in/out from the static table (all values estimates, labeled as such).
  // Unknown models say so honestly instead of guessing.
  function updateModelIntel() {
    var el = $("modelIntel");
    if (!el) return;
    var id = currentModelValue();
    if (id === MANUAL) id = $("model").value.trim();
    var sm = setupMeta();
    var line = sm ? sm.modelIntelLine(id) : null;
    el.hidden = false;
    if (!id) {
      el.textContent = "Pick a model to see context-window and pricing estimates.";
    } else if (line) {
      el.textContent = line;
    } else {
      el.textContent = "Context/pricing unknown for this model \u2014 check the provider docs.";
    }
  }

  // (b) "Get {Provider} API Key" buttons — straight to key creation, per the
  // Roo/Cline pattern. The extension host allowlists the destination.
  function wireGetKeyButton(id, providerId) {
    var b = $(id);
    if (!b) return;
    b.addEventListener("click", function () {
      var sm = setupMeta();
      var p = sm ? sm.providerById(providerId) : null;
      openExternal(p && p.keyUrl);
    });
  }
  wireGetKeyButton("openaiGetKey", "openai");
  wireGetKeyButton("anthropicGetKey", "anthropic");
  wireGetKeyButton("bedrockGetKey", "bedrock");

  var docsLink = $("providerDocs");
  if (docsLink) {
    docsLink.addEventListener("click", function (e) {
      if (e && e.preventDefault) e.preventDefault();
      openExternal(currentDocsUrl);
    });
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
    updateModelIntel();
  }

  $("provider").addEventListener("change", function () {
    updateFetchUi();
    updateProviderDocs();
  });

  $("model").addEventListener("change", updateModelIntel);
  $("model").addEventListener("input", updateModelIntel);

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
    updateModelIntel();
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
    } else if (m.type === "bindingChanged") {
      // Spec 4.3: the live binding changed (connect/reconnect/disconnect/
      // dirty transition) — refresh the full state so the "current binding"
      // section always matches the authoritative session.binding.
      vscode.postMessage({ type: "init" });
    } else if (m.type === "reconnected") {
      $("envResult").textContent = m.ok ? "reconnected" : "reconnect failed — see output channel";
      vscode.postMessage({ type: "init" }); // refresh state
    } else if (m.type === "saveFailed") {
      // A key could not be written to secret storage (e.g. no working
      // system keyring): surface it in the panel instead of hanging on
      // "reconnecting…". Non-secret settings were already saved.
      status.textContent = "Could not save API key: " + (m.error || "unknown error");
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
