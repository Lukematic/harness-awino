# Harness Awino

[![Download beta VSIX](https://img.shields.io/badge/download-beta%20VSIX-blue)](https://github.com/Lukematic/harness-awino/releases)

**Try the VS Code extension (beta):** one button downloads the `.vsix` from
[Releases](https://github.com/Lukematic/harness-awino/releases) (pre-release),
one command installs it:

```sh
VSIX_URL=$(curl -s https://api.github.com/repos/Lukematic/harness-awino/releases | python3 -c "
import json,sys
for r in json.load(sys.stdin):
    for a in r.get('assets', []):
        if a['name'].endswith('.vsix'):
            print(a['browser_download_url']); sys.exit()
print('NO_VSIX_FOUND'); sys.exit(1)
") && curl -sSL -o awino-beta.vsix "$VSIX_URL" && code --install-extension awino-beta.vsix --force
```

(Beta channel only — the Marketplace one-click install is a later launch step.
See `integrations/vscode/extension/RELEASING.md` for the release process.)

A.W.I.N.O. rebuilt as the **automatic per-turn loop owner** — not a CLI the
model may or may not call, but the `while` loop that owns every turn:
contract compiled from code-owned state, mode × stance × skill routed per
turn, permissions gated before execution, a separate judge on every turn,
approvals bound to exact arguments, and completion computed from evidence
(never from claims).

## Contents

- `BUILD_SPEC.md` — the build specification. Authoritative; supersedes
  `ARCHITECTURE.md`.
- `CAPABILITY_REGISTRY.md` — every mode, stance, skill, intent route, and
  floor default, plus the legacy-skill mapping and gaps.
- `AUTHORING_TEMPLATE.md` — the required template and test contract for
  adding a mode, stance, or skill.
- `prototype/` — Phase 0 implementation. Standard library only, mock model
  backends only, no network, no paid APIs.
- `proof/` — `proof_session.py` drives one mission against an adversarial
  model for ten turns and writes `TRANSCRIPT.md`: every attack the loop
  blocked, with the event-log evidence.

## Run it

```sh
cd prototype && ./run_tests.sh        # full suite: routing, enforcement, judge, approvals, recovery, plug-and-play
cd proof && python3 proof_session.py  # adversarial session -> TRANSCRIPT.md (8/8 verdicts)
```

## Status

Phase 0 (deterministic enforcement, mock backends): done and proven.
Phase A (live model backend, human approval interrupt, kill/restart against
a real provider): done and proven (7B live turn, approval recovery after
SIGKILL, mid-effect reconciliation).
Phases B–E (immutable contracts, fail-closed skills, worker isolation,
backend selection, rollback): done and proven.
VS Code extension (`integrations/vscode/`): proven in a live GUI test
(34/34 checks, real approval deny path) — not yet published.
MCP server (`integrations/mcp-server/`): standalone, client-agnostic —
contract compiler, turn validator, judge panel, skill synthesis.
**Plug-and-play projects (unreleased)**: auto-init on session start,
startup checklist with venv/just/ruff provisioning, `.awino/project.yaml`
source of truth, memory registry, task DAG store, five intelligent role
modes with a deterministic router, role environment profiles, skill egress
audit, and a hard verification gate (separate verifier worker; no pass
verdict, no REVIEW). See `CHANGELOG.md`.
