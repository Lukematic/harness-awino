# A.W.I.N.O. VS Code Extension

The agent IDE surface for A.W.I.N.O. — Kilo-class UX, harness-grade engine.

## What this is

A VS Code extension that puts the loop-owner harness inside the editor:
chat, contract view, journal, skills, context, modes. The **Python sidecar**
(`prototype/awino_sidecar.py`) owns the turn loop in code — the extension
is a surface and cannot bypass the harness.

## Layout

| Path | What |
|------|------|
| `package.json` | Extension manifest: views, commands, `awino.*` settings. Publisher placeholder `lukematic`. |
| `tsconfig.json` | Strict TS config. `tsc` must compile clean. |
| `src/sidecar.ts` | **vscode-free** sidecar client (JSON-lines protocol). Exercised headless by `test/harness.js`. |
| `src/views.ts` | Tree data providers: Contract, Journal, Learnings, Skills, Context, Modes. |
| `src/extension.ts` | Activation: spawns sidecar, routes §4 events, commands, status bar, doctor, models panel. |
| `webview/chat.html` + `chat.js` | Mission chat. Plain JS, no build step. Renders every §4 event; approval cards with diffs. |
| `webview/models.html` + `models.js` | Models & Providers panel: provider/endpoint/model/timeout, environment switcher, keys via SecretStorage. |
| `resources/awino.svg` | Activity bar icon. |
| `test/harness.js` | Node harness: imports compiled `out/sidecar.js`, spawns the real sidecar, asserts hello→ready, turn_result, approval round-trip (approve + deny), fail-closed on unknown commands. |

## Key design facts

- API keys are **never** in settings JSON. They live in VS Code SecretStorage
  and reach the sidecar via env vars only. They never appear in events, logs,
  or the contract block.
- Environments come from the project's `.awino/providers.yaml`; switching is
  explicit and journal-logged. Project A can never see project B's key.
- Approval flow: `approval_requested` → VS Code modal (Approve / Deny /
  "Always allow this tool" for the session). `write_file` approvals carry a
  unified diff. Deny/dismiss is the safe direction.
- Compaction at ~85% of the context window pauses the turn and asks the
  operator (modal + webview card). Denial is journaled; auto-approve is
  per-project and defaults off.
- Modes are data: stage defaults switch automatically; custom modes
  (planner, architect, debug-engineer, storyboard, tutor, …) are operator
  overlays, journal-logged.
- Skill-as-persona is a lens, not a license: only verified + SHA-256-pinned
  skills can be assumed, and the persona can never widen the contract's
  tool policy.

## Build & test

```bash
npm install            # devDeps only: typescript + @types/* (zero runtime deps)
npx tsc -p ./          # must compile clean
node test/harness.js   # spawn-path test against the real sidecar
npx @vscode/vsce package   # build the .vsix (result recorded honestly)
```

## Honest gaps

- The extension has **not** been exercised inside a live VS Code window —
  the GUI half is compiled, not run. The sidecar half is fully tested
  (295 python tests + the node harness).
- Marketplace publishing needs the user's identity — steps are in
  `integrations/vscode/README.md`; the task stops at a built `.vsix`.
