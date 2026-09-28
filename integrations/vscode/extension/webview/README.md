# webview

The extension's web UIs. Plain HTML and JavaScript: no build step, no
framework. The extension host serves them with a strict Content Security
Policy.

| File | What it does |
|---|---|
| `chat.html`, `chat.js` | Mission Chat. Renders sidecar events: replies (with a small built-in markdown renderer), streaming text, tool results as one plain line, approval cards with diffs and shell targets, receipt cards, errors. Also the first-run setup card. Sends your messages, approval decisions and Stop to the extension host. |
| `models.html`, `models.js` | Models & Providers panel: provider (echo, ollama, openai, anthropic, bedrock), endpoint, model with "Fetch models", timeout, key status and the environment switcher. Keys go to SecretStorage through the extension host. |
| `setup-shared.js` | Shared by both: the provider list (docs and key links), which providers need a key, and model hints. Holds no secrets. |

The webview never builds turns, calls tools or edits files. The loop runs
in the Python sidecar.

Tests: `../test/chatSetup.js`, `streaming.js`, `markdown.js`,
`receiptCard.js`, `toolSummary.js`, `harnessReply.js`, `modelsPanel.js`,
`setupShared.js` and `cspPlaceholders.js` run these files against a small
DOM shim in node.
