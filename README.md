# Harness Awino

[![Download beta VSIX](https://img.shields.io/badge/download-beta%20VSIX-blue)](https://github.com/Lukematic/harness-awino/releases)

A.W.I.N.O. rebuilt as the **automatic per-turn loop owner**. Not a CLI the
model may or may not call, but the `while` loop that owns every turn: the
contract (objective → mission → tools → progress) is compiled from
code-owned state, the mode × stance × skill is routed per turn, permissions
are gated before execution, a judge panel scores every turn, approvals are
bound to the exact plan, and completion computed from evidence — never
from claims.

Two things live here:

- **`prototype/`** — the harness itself. Standard-library Python. This is
  the engine; everything else is a surface.
- **`integrations/vscode/extension/`** — the VS Code extension, the product
  surface. Chat, contract view, journal, skills, modes, providers. It talks
  to the harness through the Python sidecar and cannot bypass it.

## Run it

```sh
cd prototype && ./run_tests.sh   # full harness suite (909 tests, ~3 min; point TMPDIR at a roomy dir)
cd integrations/vscode/extension && npm test   # extension suite (node)
```

## Install the extension (beta)

Tagged prereleases on the
[releases page](https://github.com/Lukematic/harness-awino/releases) carry
the `.vsix`. One command downloads and installs the latest beta:

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

or in VS Code: Extensions view → `…` → *Install from VSIX…* with the
downloaded file. No Python setup needed — the runtime ships inside the
package.

## What's in the harness

| Piece | Where | What it does |
|---|---|---|
| Per-turn contract loop | `prototype/contract_loop.py`, `loop.py` | Compiles and validates the turn contract pre-turn and pre-execute; refuses broken contracts with named reasons |
| Modes (5) | `prototype/contract.py::MODES` | observe, plan, build, verify, ship — permission profiles, computed before the model acts |
| Stances (9) | `prototype/stances.py` | steel-man, feynman, planning-grill, first-principles, premortem, devil's-advocate, advisor, triage, verifier — reasoning procedures with rubrics. The model picks one each turn and says why; the phase allows only some, and its floor rubric always runs (PLAN/BUILD first-principles, VERIFY devil's-advocate, REVIEW/SHIP premortem). Trigger words are the fallback |
| Skills (53) | `prototype/skills/` | Injected knowledge, SHA-256 pinned, verified at load. Core loop, rigor coach, osmani port, agent personas, references, durable-memory, debug, rpi |
| Judge panel | `prototype/judges.py` | N judges vote by quorum per turn; no single gatekeeper; fail-closed |
| Skill synthesis | `prototype/synthesis.py` | Learnings → sandbox verification → pin → admit only on pass |
| Fan-out | `prototype/loop.py` | Parallel workers with atomic overlap/budget checks and a fail-closed synthesis barrier; per-worker backend/model routing via a code-owned routing map |
| Approvals | `prototype/loop.py` (`approve`, `_has_valid_approval`) | Consequential calls pause for approval bound to the exact arguments, mission revision and scope epoch; a scope change invalidates pending approvals. Shell cards show cwd-resolved absolute targets and flag out-of-workspace addressing |
| patch_file | `prototype/tools.py` | Atomic, fail-closed unified-diff application (named refusal codes); build-mode only, consequential like `write_file` |
| Secret redaction | `prototype/secret_redaction.py` | High-confidence credential shapes redacted at the journaling boundary and on sidecar log output; general PII scrubbing out of scope by design |
| Durable memory | `prototype/memory_store.py` | Local-first JSONL store (`.awino/memory.jsonl`), chunked entries, content-hash dedup |
| Bedrock SigV4 | `prototype/awino_sidecar.py` | Stdlib-only SigV4 from the AWS credential chain (env / `~/.aws` / SSO via GetRoleCredentials); fail-closed named errors; first live call still untested here |
| Discovery interview | `prototype/skills/discovery.md` | Fires on new tasks; grill enforces one-question-at-a-time, ask-XOR-advance |
| MCP server | `integrations/mcp-server/` | Standalone, client-agnostic: contract compiler, turn validator, judge panel, skill synthesis |
| Mission loop | `prototype/loop.py` | Recursive model → tool → result rounds. A write moves BUILD → VERIFY; a failing test run routes back to BUILD to repair (three identical failures trip the three-strike rethink); a pass spawns an independent verifier; only evidence moves REVIEW → SHIP. Proven end to end in `tests/test_e2e_mission.py` |
| Stories + brag board | `prototype/story.py`; VS Code *Stories* panel | Each session works one story (problem, plan, done criteria, branch, time spent). `story_plan` records the plan agreed with the user — Honda first, Bugatti pitched in brief. Closed stories land on the brag board with date and outcome. Open stories and due parked ideas show at session start |
| Stretch goals | `stretch_goal` tool | The harness pitches a bigger idea as Need / Approach / Benefits / Competition with 3–5 steps; it is parked with a revisit date and never built unasked |
| Project memory | `<project>/.awino/` | Stories, registry, and session journals live in the project, shared by the CLI and the extension; journals are gitignored |

Full inventory: [`CAPABILITY_REGISTRY.md`](CAPABILITY_REGISTRY.md).

## Docs

- `ARCHITECTURE.md` — the system as built.
- `CAPABILITY_REGISTRY.md` — every mode, stance, skill, intent route, and floor default, plus the legacy-skill mapping and open gaps.
- `AUTHORING_TEMPLATE.md` — the required template and test contract for adding a mode, stance, or skill.
- `BUILD_SPEC.md` — the original build specification. **Complete**; kept for history.
- `docs/ADAPTER_CONTRACT.md` — the provider-neutral controller protocol and the Context → Interactive → Autonomous-local → Hosted conformance ladder.
- `docs/SKILL_ADMISSION.md` — how a new skill gets admitted (and the bar for adding one).
- `CHANGELOG.md` — what changed, by release.
- `integrations/vscode/extension/RELEASING.md` — how a beta VSIX gets cut.
- `proof/` — `proof_session.py` drives one mission against an adversarial model and writes `TRANSCRIPT.md`: every attack the loop blocked, with event-log evidence.

## Status

`main` carries 0.7.0: the end-to-end mission loop (write → test → repair →
verify → ship), model-chosen stances within phase limits, stories and the
brag board in VS Code, stretch goals, readable chat, plain-language errors,
and one project memory shared by the CLI and the extension. What is proven
and how: [`docs/BUILD_PLAN_v0.7.md`](docs/BUILD_PLAN_v0.7.md) (with an
audit of every request so far) and `proof/demo-agentic-learning/` (a
recorded sandbox mission). The VSIX installs and passes the GUI proof in
real VS Code on Windows (CI); it has not yet run a mission against a live
model — that is the release gate. Betas ship from `vsix-v*` tags.

Product name is still open — "Awino" appears throughout as the working name,
not the final brand.
