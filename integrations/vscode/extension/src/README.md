# src

TypeScript for the extension host. `npm run compile` (`tsc -p ./`, strict)
builds it into `out/`; it must compile clean.

Most modules do not import `vscode`, so the node tests in `../test/` can load
the compiled `out/*.js` directly.

| File | What it does |
|---|---|
| `extension.ts` | Activation. Starts the sidecar, routes its events to the chat, views and status bar, and registers every command. Also the Models & Providers panel, Doctor, setup offers and story flows. Imports `vscode`. |
| `sidecar.ts` | Client for `prototype/awino_sidecar.py`: one JSON object per line on stdin and stdout. Finds the bundled sidecar, or the repo copy during development. |
| `views.ts` | Tree views: Contract, Journal, Learnings, Skills, Context, Modes, Tasks, Stories. Each asks the sidecar through the `command` verb. Imports `vscode`. |
| `python.ts` | Picks the Python to run the sidecar: `awino.pythonPath` if set, then the bundled runtime, then PATH. |
| `bundledPython.ts` | Finds the Python runtime bundled in the `.vsix` (`python/<platform>/`). |
| `pythonRecovery.ts` | The "Locate Python..." flow when no interpreter is found. |
| `connectGuard.ts` | Makes sure only one sidecar connect runs at a time. |
| `providerKeys.ts` | Which provider needs which key, and whether it is missing. |
| `modelDiscovery.ts` | "Fetch models" for the Models & Providers panel (OpenAI-compatible and Ollama list endpoints). |
| `bedrock.ts` | Bedrock endpoint, region and ARN helpers, and the connection test. |
| `connection_importer.ts` | "Import connections from other tools": scans Claude Code, Kilo CLI and `.env` files. Never copies secrets. |
| `chatHistory.ts` | Keeps the chat transcript when VS Code hides and re-creates the chat view. |
| `nativeApply.ts` | Applies approved writes through `WorkspaceEdit` (one undo step) and checks the file did not change first. Imports `vscode`. |
| `awinoTerminal.ts` | Runs approved `run_command` calls in a VS Code terminal and streams output back. Imports `vscode`. |
| `delegatedPure.ts` | Pure helpers for the two files above (hashing, ANSI stripping, path checks). |

The GUI checks for `nativeApply.ts` and `awinoTerminal.ts` are listed in
[docs/history/NATIVE_APPLY_GUI_ASSERTIONS.md](../../../../docs/history/NATIVE_APPLY_GUI_ASSERTIONS.md).
