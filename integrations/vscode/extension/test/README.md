# test

Node tests for the extension. No test framework and no VS Code instance:
each file is a plain script that exits non-zero on failure.

Run them all with `npm test` after `npm run compile` (the tests load the
compiled `out/*.js`). The list and order are in `package.json` under
`scripts.test`. Run one with `node test/<name>.js`.

| Group | Files |
|---|---|
| Sidecar protocol (spawns the real Python sidecar; needs `python3`) | `harness.js`, `delegatedApply.js` |
| Rigor coach surface | `rigor.js` |
| Chat webview (DOM shim) | `chatSetup.js`, `streaming.js`, `markdown.js`, `harnessReply.js`, `receiptCard.js`, `toolSummary.js`, `cspPlaceholders.js`, `chatHistory.js` |
| Models & Providers | `modelsPanel.js`, `setupShared.js`, `modelDiscovery.js`, `providerKeys.js`, `bedrock.js`, `connection_importer.js` |
| Python selection and connect | `python.js`, `pythonRecovery.js`, `bundledPython.js`, `connectGuard.js` |
| Views | `tasksView.js`, `storiesView.js` |
| Packaging | `bundleSidecar.js`, `bundlePython.js`, `vsixContents.js` |

Two packaging tests skip with a message on a fresh checkout:
`bundlePython.js` until `node scripts/fetch-python-runtimes.js` has run, and
`vsixContents.js` until a `.vsix` has been built.
