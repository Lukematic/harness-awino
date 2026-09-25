# Competitor UX Teardown — Awino vs Roo / Kilo / Cline / Copilot Chat

**Date:** 2026-09-23
**Question:** "Can Awino beat the existing tools?"
**Method:** Hands-on testing in real VS Code 1.139.0 (Xvfb, Playwright CDP). Each extension installed isolated, launched fresh, first-run walked click by click with screenshots. No authentication, no paid API calls. Live model responses could not be tested without keys — streaming/thinking/tool-call display for competitors is assessed from static UI + setup flows, and flagged where unverified.

**Versions tested:** Roo Code 3.54.0 · Kilo Code 7.7.9 · Cline 4.1.20 · GitHub Copilot Chat 0.48.1 · Awino 0.3.0 ("A.W.I.N.O. Loop Owner")

**Evidence:** `~/workspace/awino-rebuild/proof/competitor-teardown/{roo,kilo,cline,copilot,awino}/`

---

## Headline verdict

**Awino 0.3.0 loses on every setup dimension that matters to a new user, and the losses are all in the first 60 seconds.**

The blunt version:

- **Roo does provider setup in 2 clicks with a guided wizard. Awino takes "infinite" clicks** — the Models & Providers panel is hidden behind a command-palette command (`A.W.I.N.O.: Models & Providers`) that appears nowhere in the UI. The first-run sidebar never mentions it. A new user cannot discover how to add a key.
- **Roo and Cline both have a "Get {Provider} API Key" button** that takes you straight to key creation. Awino has no link of any kind — you must already have a key and know where to put it.
- **Roo and Cline fetch the model list from the provider** (searchable picker). Awino is manual text entry only, prefilled with a stale `qwen2.5:1.5b`.
- **Cline shows per-model pricing and context window** next to the picker. Awino shows nothing.
- **The 0.3.0 Provider dropdown renders EMPTY** (screenshot proof) — the current binding ("provider: scripted") is shown in a text box above, but the dropdown itself displays blank. It looks broken on first open.
- **Windows users never even get this far**: `awino.pythonPath` defaults to `python3`, which stock Windows doesn't have (`python`/`py` instead), so the sidecar shows "not connected" with no actionable error. The user already hit this. It's a knockout blocker.
- Awino's chat *works* (scripted provider responded), but the response is raw harness contract text dumped as a block — no streaming, no markdown polish, no thinking trace, squeezed into a narrow sidebar strip above six tree views. Roo/Cline/Kilo/Copilot all make the chat the entire sidebar.

None of these are hard problems. They're all "someone polished the first-run" problems. That's what 0.4.0 needs to be.

---

## Per-tool findings

### 1. Roo Code 3.54.0 — the onboarding benchmark

**First run** (`roo/02_first_run.png`): Full-sidebar welcome — "Welcome to Roo Code!" with a prominent **Get Started** button and an **Import Settings** secondary action. One sentence of what the product does. No tree views, no jargon.

**Click path to key entry: 2 clicks.**
1. Click the Roo activity-bar icon
2. Click **Get Started**
→ "Choose your provider" wizard (`roo/03_provider_setup.png`)

**The wizard** (`roo/03_provider_setup.png`, `roo/04_provider_dropdown.png`):
- "Roo needs an LLM provider to work. Choose one to get started, **you can add more later**." (multi-profile support stated up front)
- **API Provider** searchable dropdown — **25 providers**: OpenRouter, Amazon Bedrock, Anthropic, Baseten, DeepSeek, Fireworks AI, GCP Vertex AI, Google Gemini, LiteLLM, LM Studio, MiniMax, Mistral, Moonshot, Ollama, OpenAI, OpenAI - ChatGPT Plus/Pro, **OpenAI Compatible**, Poe, Qwen Code, Requesty, SambaNova, Unbound, Vercel AI Gateway, VS Code LM API, xAI (Grok), Z.ai. Search box at the top of the list.
- **"Provider Docs" link** next to the dropdown.
- Inline validation in red: **"You must provide a valid API key."**
- Key field with placeholder "Enter API Key..." + note: **"API keys are stored securely in VSCode's Secret Storage"**
- **"Get OpenRouter API Key" button** — direct link to obtain a key. This is the single best discoverability pattern in the group.
- **Model** searchable dropdown, prefilled with a sane default (`anthropic/claude-sonnet-4.5`). Opening it shows a search box; the list **fetches live from the provider** (empty until a key is entered — `roo/05_model_dropdown.png`).
- **Back / Finish →** buttons. Clicking Finish with no key keeps you on the wizard (validation blocks — `roo/06_finish_validation.png`). Fail-closed, with the reason shown inline.

**Chat UI:** Could not test live (no key). Static UI is a full-sidebar Copilot-style chat. Roo is known for visible thinking/tool-call blocks, but that is **unverified hands-on** — flagged, not claimed.

**Scores:** Setup 10 · Key discoverability 10 · Model picker 9 (fetched, but empty without key) · Multi-key 8 (profiles promised; labels not seen) · Onboarding 10.

### 2. Kilo Code 7.7.9 — chat-first, zero wizard

**First run** (`kilo/02_first_run.png`): **No welcome, no wizard at all.** Straight to the chat UI with "Initializing..." → a chat input at the bottom containing a **"No providers" pill button** directly in the input bar.

**Click path to provider setup: 1 click** (the "No providers" pill) — the most discoverable entry point in the group *when it works*. But there is no guidance, no docs link, no "get a key" button visible.

**Distribution problem found hands-on:** Kilo's server binary (`bin/kilo`) failed to start on this Linux box with a clear error surfaced in the chat UI: "Server connection failed: CLI path …/bin/kilo … CLI process exited with code 2 … Syntax error: "(" unexpected" (`kilo/05_server_failed.png`). Root cause: the Marketplace "latest" VSIX bundles a **Mach-O 64-bit arm64 (macOS) binary** — `file` confirms it. Platform-specific `?targetPlatform=` requests for linux-x64 and others all 404; Kilo publishes a single universal VSIX whose bundled binary is macOS. On a real install via VS Code's installer the platform resolution may differ, but **what the gallery hands out by default does not run on Linux x64**. The error message is honest and offers **Retry**, which is good failure UX — but the provider dropdown is dead until the server runs, so setup could not be walked further.

**Scores:** Setup 5 (1-click entry, but no wizard and server-dependent) · Key discoverability 6 (pill is visible; nothing beyond it verified) · Model picker unverified · Multi-key unverified · Onboarding 3 (none).

### 3. Cline 4.1.20 — the choice-driven onboarding

**First run** (`cline/02_first_run.png`): "How will you use Cline?" — four radio paths:
- **Absolutely Free** (preselected) — "Get started at no cost"
- **ClinePass** — "Low cost subscription plan for best open weights model. Learn more"
- **Frontier Model** — "Claude, GPT Codex, Gemini, etc."
- **Bring my own API key** — "Use Cline with your provider of choice"
plus **Continue**, a **Login to Cline** link, and "You can change this later in settings." This is the best "which user are you?" triage in the group.

**Click path to key entry (BYO): 3 clicks** — icon → "Bring my own API key" radio → Continue.

**"Configure your provider"** (`cline/04_key_entry.png`) — the richest provider screen tested:
- API Provider dropdown
- Key field ("Enter API Key...") + **"Get OpenRouter API Key" button**
- "This key is stored locally and only used to make API requests from this extension."
- **Model selector** (pill with × to clear) with a **"Switch to 1M context window model"** link
- **Model intelligence panel**: plain-English description, **Context: 200K, Input: $2/M, Output: $10/M** — pricing transparency no one else in the group has
- **Adaptive Thinking / Reasoning Effort** dropdown (None) — explicit reasoning-effort control, i.e. the "visible thinking" dial exposed as a setting
- **ADVANCED** collapsible: Images, Browser, Prompt Caching + cache pricing, Provider Routing, per-mode models ("Use different models for Plan and Act modes")
- **Continue / Back**

**Scores:** Setup 9 · Key discoverability 9 · Model picker 9 (curated + priced; live-fetch not confirmed) · Multi-key 6 (not verified) · Onboarding 9.

### 4. GitHub Copilot Chat 0.48.1 — the OAuth wall

No activity-bar icon of its own; it drives VS Code's **native Chat panel** (right side). Clicking **Sign In** opens the wall (`copilot/04_signin_wall.png`): "Sign in to use GitHub Copilot" — **Continue with GitHub / Google / Apple / GHE.com**, Terms + Privacy links. **Stopped here per constraints — no authentication performed.**

The chat panel itself is the smoothness benchmark: "Build with Agent", model picker ("Models"), Agent mode selector, "Describe what to build" input. Zero API-key UX by design — OAuth replaces it. Nothing to learn for Awino on key setup; everything to learn on chat polish.

**Scores:** Setup 8 (1 click to wall; then account-dependent) · Key discoverability N/A (OAuth) · Model picker 8 (native) · Onboarding 6 (the wall *is* the onboarding).

### 5. Awino 0.3.0 — what the user actually got

**First run** (`awino/02_first_run.png`): Clicking the A.W.I.N.O. activity-bar icon shows **seven tree views** (Mission Chat, Contract, Journal, Learnings, Skills, Context, Modes) and a chat webview squeezed at the top. The Mission Chat hint reads: "No active mission. Say what you want to build and the harness opens its discovery interview. Or run A.W.I.N.O.: New Mission / New Mission from Seed from the command palette."

**What is missing:** any mention of provider setup, API keys, or models. Anywhere. The words "API key" do not appear in the first-run UI.

**Click path to key entry: undiscoverable.** The only path is Ctrl+Shift+P → type "A.W.I.N.O.: Models & Providers" (`awino.openModels`) — a command the user must already know exists. There is no button, no link, no hint, no onboarding. Compared to Roo's 2 clicks, Awino's setup might as well not exist for a new user.

**The Models & Providers panel** (`awino/04_models_providers.png`) — an editor-tab webview:
- "Current binding" box: `provider: scripted · model: ?` / `key: not-required`
- **Provider dropdown renders EMPTY** — blank field. The binding text above says "scripted", but the dropdown shows nothing selected. First impression: broken.
- Provider options: echo (safe default) · ollama · openai-compatible · anthropic · bedrock
- Bedrock region dropdown (30+ regions), Endpoint field (prefilled `http://localhost:11434`), **Model field (manual text entry, prefilled `qwen2.5:1.5b` — stale)**
- **No model fetch/discovery.** No "Fetch models" button. No dropdown of available models.
- Key sections per provider ("OpenAI-compatible API key (not set)" etc.) with placeholder "stored in VS Code SecretStorage — never in settings" — but **no visible affordance for HOW to set the key**, no "Get API key" link, no docs link
- "Save & Reconnect" / "Clear stored keys" buttons (below fold)
- **No key labels/aliases.** No multi-key beyond one-per-provider.
- Timeout field (180s) — engineer-facing, not user-facing

**Chat** (`awino/06_chat_typed.png`, `awino/07_chat_response.png`): Sending "hello awino, what can you do?" through the scripted provider returns a **single block of raw harness contract text**: `[A.W.I.N.O. | phase: IDLE | mode: observe | stance: advisor | …] | STANCE -> advisor (default) | Script exhausted; awaiting direction. | …` — no streaming observed, no markdown rendering, no thinking trace, no tool-call display. It works, but it reads like a log line, not a chat.

**The Windows blocker (user-reported, code-confirmed):** `src/extension.ts` defaults `awino.pythonPath` to `python3`. Stock Windows exposes `python`/`py`, not `python3` — so the sidecar never starts and the status sits at "not connected" with no actionable error. On this Linux box the sidecar connected fine ("Sidecar ready — provider scripted"), which is exactly why this slipped through: **the happy path was only ever tested where `python3` exists.**

**Scores:** Setup 1 · Key discoverability 2 · Model picker 2 (manual entry) · Multi-key 3 (one key per provider, no labels) · Chat smoothness 3 (works; block-appended raw text) · Thinking visibility 1 (none in chat) · Onboarding 1 (none).

---

## Scored comparison

| Dimension | Roo 3.54.0 | Kilo 7.7.9 | Cline 4.1.20 | Copilot Chat 0.48.1 | **Awino 0.3.0** |
|---|---|---|---|---|---|
| Setup friction (clicks to key entry) | **10** (2) | 5 (1, server-gated) | **9** (3) | 8 (OAuth wall) | **1** (undiscoverable) |
| Key discoverability | **10** | 6 | **9** | N/A | **2** |
| Model picker (fetched vs manual) | **9** fetched | unverified | **9** curated+priced | 8 native | **2** manual |
| Multi-key / labels | 8 profiles | unverified | 6 | N/A | **3** |
| Chat smoothness | unverified live | unverified | unverified live | **10** benchmark | **3** |
| Thinking visibility | unverified live | unverified | **8** (effort dial) | 7 native | **1** |
| Onboarding | **10** | 3 | **9** | 6 | **1** |

**Live-response caveat:** no paid API calls were made, so streaming smoothness, thinking-trace rendering, and tool-call display for Roo/Kilo/Cline could not be verified hands-on. Scores there reflect setup + static UI only. Copilot's chat is the stated smoothness benchmark by user direction.

---

## Ranked gap list (by user pain)

1. **Provider setup is undiscoverable.** No onboarding, no button, no hint. The command exists but nothing points to it. *(Pain: total — new users churn here.)*
2. **Windows "not connected" with no actionable error.** `python3` default doesn't exist on stock Windows. The user hit this; it's the reported blocker. *(Pain: knockout.)*
3. **Manual model entry, no discovery.** User explicitly asked: "query the endpoint and show available models." Roo/Cline both do this. *(Pain: high — every key-holder hits it.)*
4. **No key labels/aliases.** User explicitly asked. *(Pain: medium-high.)*
5. **No "get a key" path.** Roo/Cline link straight to key creation; Awino assumes you already have one. *(Pain: medium — first-time key buyers stall.)*
6. **Provider dropdown renders blank** on first open (0.3.0 bug). *(Pain: medium — looks broken.)*
7. **Chat is a raw contract dump**, no streaming, no markdown polish, no thinking trace, squeezed above six tree views. *(Pain: medium — but it's the daily surface.)*
8. **No provider docs links, no model pricing/context info** (Cline's $/M + context panel). *(Pain: low-medium — trust/polish.)*
9. **No first-run triage** (Cline's "how will you use this?" / Roo's wizard). *(Pain: low-medium — guidance.)*

---

## Fix mapping

### → 0.4.0 (chat + provider-panel UX — active build)

The active 0.4.0 scope (Wakandan chat, setup card, `/models` + Ollama `/api/tags` discovery, key labels, `py`→`python`→`python3` fallback with real errors, rename to Awino) already covers gaps **2, 3, 4** and partly **1** (setup card in chat) and **7** (chat overhaul). A concurrent in-progress 0.4.0 GUI run visibly shows the Vibranium theme, "No model connected → Set up provider" card, "Fetch models", and label fields — direction confirmed, completion still to be proven by the owning build.

**New from this teardown — still needed in 0.4.0:**
- **a. First-run onboarding wizard** (Roo/Cline pattern): on first activation with no provider configured, the sidebar should BE the setup flow — "Choose your provider → key → model → done" — not a card you have to notice. The setup card is a patch; the wizard is the fix. (Gap 1, 9)
- **b. "Get {Provider} API Key" button** linking to key creation, per provider. Copy Roo/Cline verbatim. (Gap 5)
- **c. "Provider Docs" link** next to the provider dropdown. (Gap 8)
- **d. Fix the blank provider dropdown** — it must show the current binding on open. (Gap 6)
- **e. Model intelligence line** under the picker: context window + $/M in/out (Cline pattern), populated from discovery where available. (Gap 8)
- **f. Provider status pill in the chat input** (Kilo's "No providers" pattern): the binding state should be visible where the user types, not only in a panel. (Gap 1)

### → 0.5.0 (architecture)

- **g. Eliminate the Python prerequisite: freeze the stdlib sidecar into per-platform executables bundled in the VSIX.** The sidecar is stdlib-only, so this is feasible (PyInstaller/cx_Freeze/Nuitka or equivalent). Extension installs and runs with zero interpreter configuration. This is the structural answer to gap 2 — the `py`→`python`→`python3` fallback in 0.4.0 is triage; the bundled executable is the cure. Measured against Roo/Cline/Kilo: install-and-work, no interpreter questions asked.
- **h. Honest failure UX if the executable can't launch** (Kilo pattern): surface the real spawn error + fix hint in the chat UI, never a bare "not connected".

### Deliberately not copied

- **Copilot's OAuth model** — wrong for a BYO-key multi-provider tool.
- **Cline's 4-path triage** as-is — Awino's provider-first wizard covers it; the "Absolutely Free" path maps to the scripted/echo default + Ollama.
- **Kilo's native-binary server** — Kilo's own distribution shows the risk (wrong-platform binary shipped in the universal VSIX). If 0.5.0 bundles executables, **ship per-platform VSIXs or verify the binary at install** — don't repeat Kilo's mistake.

---

## Appendix — evidence index

| Shot | Path |
|---|---|
| Roo welcome | `proof/competitor-teardown/roo/02_first_run.png` |
| Roo provider wizard | `proof/competitor-teardown/roo/03_provider_setup.png` |
| Roo 25-provider dropdown | `proof/competitor-teardown/roo/04_provider_dropdown.png` |
| Roo model picker (fetch) | `proof/competitor-teardown/roo/05_model_dropdown.png` |
| Roo finish validation | `proof/competitor-teardown/roo/06_finish_validation.png` |
| Kilo first run (no wizard) | `proof/competitor-teardown/kilo/02_first_run.png` |
| Kilo server-binary failure | `proof/competitor-teardown/kilo/05_server_failed.png` |
| Cline 4-path triage | `proof/competitor-teardown/cline/02_first_run.png` |
| Cline configure provider | `proof/competitor-teardown/cline/04_key_entry.png` |
| Copilot sign-in wall | `proof/competitor-teardown/copilot/04_signin_wall.png` |
| Awino 0.3.0 first run | `proof/competitor-teardown/awino/02_first_run.png` |
| Awino Models & Providers | `proof/competitor-teardown/awino/04_models_providers.png` |
| Awino chat response | `proof/competitor-teardown/awino/07_chat_response.png` |

**Test rig notes:** `~/workspace/vscode-test-env/` — `teardown_recon.py` / `teardown_deep.py` (Playwright CDP drivers), `kill_vscode.sh` (safe killer — never `pkill -f` the VS Code path from your own shell; it matches itself). One persistent VS Code per tool on distinct CDP ports (9323+); port 9222 belonged to a concurrent build — use your own ports. CDP flakiness earlier was self-inflicted (broad pkill SIGTERM'd the driver's own shell); file-based launch + PID killing is stable.
