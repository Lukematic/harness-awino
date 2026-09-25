# Releasing the Awino VS Code extension (beta channel)

This is the **beta** distribution path: a GitHub Release carrying the `.vsix`,
marked as a pre-release. One button downloads it; one command installs it:

```sh
code --install-extension awino-loop-owner-<version>.vsix
```

True one-click install (search → install → auto-update) is the VS Code
Marketplace — a later launch step, deliberately **not** this process.

## Cutting a beta release

1. Make sure `integrations/vscode/extension/package.json` `version` is the
   version you intend to ship, and that it is committed.
2. Tag and push:
   ```sh
   git tag vsix-v0.5.1
   git push origin vsix-v0.5.1
   ```
3. The `release-vsix` workflow (`.github/workflows/release-vsix.yml`) does
   the rest on an `ubuntu-latest` runner:
   - **Guard:** the tag version must equal `package.json`'s `version`.
     A mistagged release fails loudly instead of shipping a mislabeled artifact.
   - `npm ci`, then `npm run vscode:prepublish` — the **same** committed
     packaging path local builds use: `tsc` → `bundle-sidecar.js`
     (ships `prototype/` as `bundled-sidecar/`) →
     `fetch-python-runtimes.js` (downloads the pinned
     astral-sh/python-build-standalone release, verifies every archive
     SHA-256, hard-fails on mismatch; unpacks `python/<rid>/` per platform).
   - `npm test` — the extension's node suite must be green.
   - `npx @vscode/vsce package`.
   - **VSIX content assertion:** exactly one `.vsix`; contains
     `extension/bundled-sidecar/awino_sidecar.py` and the
     `extension/python/linux-x64/` runtime tree (100+ entries);
     contains **no** `.pbs-cache` tarball cache (would ~double the artifact).
     (Windows CI separately asserts the `win32-x64` tree on the 0.5.x line.)
   - `gh release create` as a **pre-release** titled
     `Awino VS Code extension v<version> (beta)`, with the `.vsix` attached.

## Before you announce it

- Download the `.vsix` from the release page yourself and install it with
  `code --install-extension` in a clean profile / a second machine.
- Confirm the sidecar spawns from the **bundled** interpreter
  (`python/<rid>/`), not a system Python.
- Walk the truthfulness checklist: connection status must reflect reality,
  provider choice must not be silently overridden, saved seeds must appear
  in Tasks. (These exact lies blocked 0.5.0 from public shipping.)
- Only then share the release link.

## Notes

- `.pbs-cache/` and `python/` are local-only build artifacts (gitignored);
  the release workflow re-fetches runtimes on every run so artifacts are
  reproducible from the tag alone.
- The `darwin-x64` (Intel Mac) runtime is fetched and hash-verified but
  excluded from the VSIX via `.vscodeignore` to keep the artifact under
  ~120MB; Apple Silicon (`darwin-arm64`) ships.
- Releases are pre-releases on purpose: the GitHub "latest release" API
  excludes them, so the README one-liner scans the release list for the
  newest `.vsix` asset instead of using `/releases/latest`.
