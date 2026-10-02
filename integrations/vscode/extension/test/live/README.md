# test/live

`hostE2E.js` runs the extension the way a user does, headless:

| Real | Stand-in |
|---|---|
| `out/extension.js` (activate, connect, SecretStorage key, message routing, commands) | the `vscode` module — only what the extension calls; any other API access fails the run ("MISSING vscode API") |
| `webview/chat.html` + `chat.js` in Chromium; the test types, clicks and screenshots | the model: `prototype/tests/fake_gateway.py`, an OpenAI-compatible server that rejects requests without the Bearer key |
| the sidecar from `bundled-sidecar/` (what the VSIX ships), spawned by the extension | |

Journeys: everyday chat (time question, New Mission, interview, aside, New
chat, key on every request); a project stuck by the 0.7.0 idle clock; a
mission that really used its turn budget (button → new mission works); a
per-turn pause (no new-mission button).

```bash
cd integrations/vscode/extension
npm run compile && node scripts/bundle-sidecar.js
NODE_PATH=<dir with the playwright package> node test/live/hostE2E.js /tmp/awino-e2e
```

Exit 0 only if every check passes; screenshots and `results.json` land in
the output folder. Not part of `npm test` (needs Playwright + Chromium).
It is not VS Code itself: the Windows GUI proof
(`.github/workflows/windows-gui-test.yml`) covers the real editor.
