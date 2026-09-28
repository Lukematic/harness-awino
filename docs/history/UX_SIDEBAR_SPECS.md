# Awino VS Code Extension — Sidebar & Setup UX Specs (target 0.5.1)

Designer-authored specs for the Engineer. Layout and information hierarchy only —
no visual flourishes. Every section states user-visible behavior precisely enough
to implement without guessing.

Status quo (verified in tree, `integrations/vscode/extension`, v0.5.0):
- 8 views in one `awino` activity-bar container, declaration order: `awino.chat`
  (webview), `awino.contract`, `awino.journal`, `awino.learnings`, `awino.skills`,
  `awino.context`, `awino.modes`, `awino.tasks` (all trees except chat).
- `onDidChangeConfiguration` for `awino.*` only logs "reconnect to apply" — no
  user-facing prompt (confirmed `src/extension.ts`).
- Status bar reads from `session.binding` (sidecar-reported); Models panel
  `webview/models.js` renders its own "Current binding" card from a `binding`
  payload; chat webview has its own provider pill. No single publish path.
- A first-run wizard already exists in `webview/chat.html` (`#wizard`,
  `wizardSave` → SecretStorage) gated by `awino.onboarded` globalState.

---

## Spec 1 — Sidebar layout: chat is the hero

### 1.1 View ordering and default states (package.json)

Keep the single `awino` activity-bar container. New declaration:

| Order | View id | Type | Default state |
|---|---|---|---|
| 1 | `awino.chat` | webview | always visible, gets majority of height |
| 2 | `awino.modes` | tree | `visibility: "collapsed"` |
| 3 | `awino.tasks` | tree | `visibility: "collapsed"` |
| 4 | `awino.contract` | tree | `visibility: "collapsed"` |
| 5 | `awino.journal` | tree | `visibility: "collapsed"` |
| 6 | `awino.skills` | tree | `visibility: "collapsed"` |
| 7 | `awino.learnings` | tree | `visibility: "collapsed"` |
| 8 | `awino.context` | tree | `visibility: "collapsed"` |

Rationale for this order: after chat, the things a user touches daily are
Modes (which agent behavior is active) and Tasks (mission queue). Mission
machinery (Contract, Journal, Skills, Learnings, Context) is inspect-on-demand
and goes last.

### 1.2 package.json changes

1. `awino.chat`: add `"initialSize": 3`. Per the VS Code views extension point,
   `initialSize` behaves like CSS flex and in the sidebar means height; it is
   honored because the same extension owns the view and the container. Value 3
   vs the default 1 gives chat roughly 3/4 of the container on first install.
2. All seven tree views: add `"visibility": "collapsed"`. This is the
   documented initial state (`"visible" | "hidden" | "collapsed"`); it applies
   only on first install — once the user expands/collapses a view, VS Code
   persists their choice and the initial value is never used again.
3. All seven tree views: add `"when": "awino:connected"`. The extension sets the
   `awino:connected` context key `true` only when the sidecar session is live
   (`session.ready` set) and `false` on disconnect/deactivate. Effect: before
   connection, the container shows ONLY the chat view — no mission machinery
   crowds the sidebar while the user is onboarding. `awino.chat` itself has no
   `when` clause (always visible).

### 1.3 Chat webview sizing rules (webview/chat.html + chat.js)

These are structural fixes for the reported "~120px height, input not visible"
symptoms — the root cause is VS Code dividing container height among 8 sibling
views plus fragile CSS, not anything JS can size around (there is no reliable
min-height API for webview views):

1. `html, body { height: 100%; }` — `#app { display: flex; flex-direction:
   column; height: 100%; }`.
2. Message list container: `flex: 1 1 auto; overflow-y: auto; min-height: 0;`.
   `min-height: 0` is mandatory — without it the flex child refuses to shrink
   and the input bar gets pushed out of view.
3. Input bar: `flex: none;` pinned at the bottom. **It must always be present in
   the DOM.** State changes may only disable it or change its placeholder text —
   never `display: none`. States:
   - Wizard showing → input disabled, placeholder "Finish setup above to start
     chatting".
   - Not connected, onboarded → input disabled, placeholder "Awino is not
     connected — check the status bar".
   - Connected → input enabled, placeholder "Message Awino…".
4. Wizard (`#wizard`) renders inside the message-list area (scrolls with it),
   never as an overlay covering the input.
5. On reload: `retainContextWhenHidden: true` is already set; in addition,
   chat.js must scroll the message list to the bottom after state rehydration
   (it already receives `postChatState` on resolve — add the scroll there).
6. Do not add any JS height measurement or resize hacks. The §1.2 changes
   (collapse + `when` + `initialSize`) are the fix for the 120px symptom.

### 1.4 Chat header content (Copilot/Roo pattern)

The chat webview header (always visible, above the message list) carries, left
to right: connection status dot, provider pill (see Spec 4), **mode selector**
(dropdown — mirrors `awino.invokeMode`; this keeps mode switching at the point
of use instead of buried in the Modes tree), `+` new-mission button. The Modes
tree view remains for full detail but is collapsed by default.

---

## Spec 2 — First-run provider onboarding

Goal: a naive user goes from install to a working chat in the minimum steps.
Plumbing exists (`#wizard` in chat.html, `wizardSave` → SecretStorage,
`awino.onboarded`); this spec defines the content, order, and behavior.

### 2.1 Entry

- On extension activation, if `awino.onboarded` is not set, the chat view shows
  the wizard full-bleed in the message area. No modal dialog (Kilo's forced
  blocking modal was filed as a UX bug — inline in the chat view, Cline-style).
- Dismissing ("Skip for now") sets `awino.onboarded = true` and enters Echo
  demo mode (see §2.4). The wizard never shows again unless the user runs the
  command `Awino: Reset Onboarding`.

### 2.2 The three steps (maximum — no more)

**Step 1 — "Choose your brain"** (single screen, radio cards):
- **Echo — demo mode** (preselected): "Local demo. No key, no network, no cost.
  Awino echoes your text so you can explore the UI. This is NOT a real AI."
- **Ollama — local models**: "Runs on your machine. No API key. Requires Ollama
  installed; default http://localhost:11434."
- **Anthropic**: "Claude models. Needs an API key from console.anthropic.com."
- **OpenAI**: "GPT models. Needs an API key from platform.openai.com."
- **OpenAI-compatible**: "Any OpenAI-style endpoint (custom base URL)."

**Step 2 — "Connect"** (fields depend on Step 1):
- Cloud providers: password-style **API key** field + **model** dropdown with a
  **"Fetch models"** button (runs the existing `runModelDiscovery` against the
  typed key/endpoint; on failure shows the error inline, e.g. "401 — check
  your key") + **endpoint** field prefilled with the provider default, editable.
- Ollama: endpoint field (prefilled `http://localhost:11434`) + model dropdown
  with "Fetch models" (lists local models). No key field at all.
- Echo: no Step 2 — goes straight to done.
- Footer text on this screen, verbatim: "Your key is stored in VS Code's
  secret storage on this machine. It is never written to settings files."
- Primary button: "Save & connect". This writes provider/model/endpoint to
  `awino.*` settings, the key to SecretStorage (existing `saveWizardSettings`
  path), then reconnects the sidecar.

**Step 3 — "Prove it"** (proof-of-life, copied from Cline's "send Hello"
pattern):
- Button "Send a test message". Sends `Hello — reply in one short sentence.`
  through the live sidecar.
- On first successful reply: `awino.onboarded = true`, wizard dismisses, the
  reply is the first message in normal chat. Done — the user is chatting.
- On failure: the error renders inline in the wizard ("Couldn't reach
  anthropic: 401 Unauthorized — your key was rejected") with two buttons:
  "Back to Step 2" and "Try Echo demo instead". The user is never left on a
  dead screen.

### 2.3 Echo truthfulness rule (fixes the beta complaint)

Wherever `provider == "echo"`:
- The chat header pill reads **"Echo (demo)"**, never just "Echo".
- A one-line banner sits directly above the input bar: "Echo is a local demo —
  replies are canned, no AI is involved. [Switch to a real provider]" — the
  link opens Models & Providers (`awino.openModels`).
- The wizard Step 1 card and the Models panel both carry the same one-sentence
  definition: "Echo is a built-in local no-op that mirrors your text. It exists
  so you can explore the UI with no key, no network, and no cost."

### 2.4 Skip path

"Skip for now" (text link, bottom of wizard) → `awino.onboarded = true`,
provider set to `echo`, normal chat opens with the Echo banner (§2.3) visible.
Skipping never produces a broken state.

---

## Spec 3 — One-click "Reconnect to apply" affordance

### 3.1 Trigger

Extend the existing `vscode.workspace.onDidChangeConfiguration` handler in
`src/extension.ts` (currently only logs). On any `awino.*` change:

1. Build a snapshot of sidecar-affecting settings:
   `awino.provider`, `awino.model`, `awino.endpoint`, `awino.timeout`,
   `awino.mcpServers`, `awino.bedrockRegion`, `awino.pythonPath`.
   (Explicitly excluded: `awino.keyLabels` — cosmetic only, refreshes the
   Models panel without a reconnect.)
2. Compare its hash to the snapshot captured at the last successful connect.
   If different → set context key `awino:settingsDirty = true` and fire the
   affordance (below). Debounce: wait 1.5s after the last change event before
   firing, so typing in settings.json doesn't spam.

### 3.2 Placement (two surfaces, both fire)

1. **Status bar item** (primary, persistent): text
   `$(sync) Awino: settings changed — reconnect to apply`,
   `backgroundColor: new vscode.ThemeColor("statusBarItem.warningBackground")`,
   `command: "awino.reconnect"`. Tooltip lists the changed keys:
   "Changed since last connect:\n• awino.model: … → …\n• awino.provider: … → …".
   Clicking reconnects immediately (one click).
2. **Notification** (transient, once per dirty batch):
   `vscode.window.showInformationMessage("Awino settings changed. Reconnect
   the sidecar to apply them.", "Reconnect now", "Later")`.
   "Reconnect now" runs `awino.reconnect`. "Later" dismisses; the status bar
   keeps the warning until reconnect. Do not re-show for the same batch; a new
   batch (hash changes again) re-arms it.

### 3.3 Reconnect behavior (`awino.reconnect`)

1. Disconnect the current sidecar session, then connect with the new settings.
2. On success:
   - Clear `awino:settingsDirty` (context key false), restore the normal
     status bar (`$(hubot) provider · env · mode`, see Spec 4).
   - Snapshot the new settings hash.
   - Show `vscode.window.showInformationMessage("Awino reconnected:
     <provider> / <model>")`.
   - **Chat history is preserved** — the chat webview keeps its messages; the
     extension re-pushes state, it does not clear the transcript.
   - Call `publishBinding()` (Spec 4) so every surface updates at once.
3. On failure:
   - Keep the dirty flag and the warning status bar.
   - Show an error notification with the failure reason and two buttons:
     "Retry" (runs `awino.reconnect` again) and "Open Models & Providers".

---

## Spec 4 — Provider binding display: single source of truth

### 4.1 The rule

**The live sidecar session's `binding` object — populated ONLY from the
sidecar's binding event / `ready` payload — is the single source of truth.**
No display surface may compute provider/model from
`workspace.getConfiguration("awino")` for display purposes. (Grep-enforceable:
display code paths must not call `getConfiguration("awino")` for
provider/model/endpoint values.)

### 4.2 Authoritative precedence

1. **Connected** (`session.ready` set): every surface shows the sidecar's
   binding. This is the truth — even if it disagrees with settings.json.
2. **Not connected**: every surface says "not connected". Nothing shows
   settings values as if active. The Models panel card switches to the
   "unapplied" variant (§4.4).
3. **Connected but settings dirty** (Spec 3 flag): surfaces show the
   last-known-good binding with a stale marker — status bar gets the `$(sync)`
   warning treatment, chat pill appends "(stale)", Models card shows a banner
   strip "Settings changed — reconnect to apply". The binding itself does not
   change until reconnect succeeds.

### 4.3 Sync mechanism

One function, `publishBinding()`, is the only writer to display surfaces.
Call it from: connect success, reconnect success, disconnect, every sidecar
binding event, and every dirty-flag transition. It does all four, atomically:

1. Updates the status bar item text + tooltip.
2. Posts `{ type: "bindingChanged", binding }` to the chat webview →
   chat.js updates the header pill and status dot.
3. Posts `{ type: "bindingChanged", binding }` to the Models panel if open →
   models.js re-renders the "Current binding" card.
4. Refreshes tree views that show binding-derived data.

### 4.4 Surface formats (character-identical where noted)

- **Status bar** (authoritative at a glance):
  `$(hubot) <provider> · <environment> · <modeId>[ · <persona>]`
  Tooltip: provider, model, environment + binding source, mode + source,
  key status ("key: present — material lives only in secret storage" /
  "key: not-required"), and connection state.
- **Chat provider pill**: `<provider> · <model>` — the exact same
  provider/model strings as the status bar, model name truncated to 24 chars
  with ellipsis. Example: `anthropic · claude-sonnet-4-…`. When dirty:
  `anthropic · claude-… (stale)`.
- **Models panel "Current binding" card** (connected): provider, model,
  environment + source, key status — same values as the status bar tooltip.
  (Not connected): card header becomes "No active connection", body reads
  "Configured values (not yet applied):" followed by the settings values
  labeled `$(info) configured, not active`, plus a "Reconnect to apply" button.

### 4.5 Acceptance test (for the Engineer)

Change `awino.model` in settings.json while connected, before reconnecting:
status bar shows the OLD model with the sync warning, chat pill shows the OLD
model + "(stale)", Models card shows the OLD binding + banner strip. After
`awino.reconnect` succeeds, all three show the NEW model with no markers.
At no point do any two surfaces disagree.

---

## Spec 5 — Teardown note: patterns to copy from Kilo / Cline / Copilot

**1. Cline — welcome-is-the-first-screen + "Done" applies instantly.**
Cline opens to a provider/API-key screen (not a dead chat), its settings gear
has provider dropdown → key field → model dropdown with a fetch button, and
clicking Done applies immediately — there is no separate reconnect step.
Guides teach "send Hello." as the proof-of-life. Copy: our wizard's Step 2
field order (provider → key → fetch models) and Step 3 test message, and the
long-term goal of making settings apply without a manual reconnect. Why: it
eliminates Awino's two biggest setup frictions — the silent Echo no-op and the
invisible reconnect requirement.

**2. Kilo — provider-first launch with Ollama as the zero-key escape hatch.**
Kilo forces provider selection on first launch and its own docs use Ollama
(host empty, model "temp") as the bypass that unblocks exploration with no
credentials. Copy: Step 1 offers Ollama as the no-key path and Echo as the
no-network path, so a naive user reaches a working chat with zero credential
friction. Do NOT copy Kilo's blocking modal — users filed it as a bug because
it blocked settings import; ours is inline in the chat view and dismissable.

**3. Copilot Chat — model/account state in the chat chrome, at the point of
use.** Copilot puts the model picker in the input bar and account state in the
header; the "what am I talking to" question is answered where you type, not in
a separate settings page. Copy: the chat header pill + stale badge (Spec 4)
and the mode selector in the chat header (Spec 1.4, Roo does the same with its
mode switcher). Why: the user should never need to open Models & Providers to
answer "which model is answering me right now."
