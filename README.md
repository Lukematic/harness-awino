# Harness Awino

A.W.I.N.O. rebuilt as the **automatic per-turn loop owner**. Not a CLI the
model may or may not call, but the `while` loop that owns every turn: the
contract (objective → mission → tools → progress) is compiled from
code-owned state, the mode × stance × skill is routed per turn, permissions
are gated before execution, a judge panel scores every turn, approvals are
bound to the exact plan, and completion is computed from evidence — never
from claims.

Two things live here:

- **`prototype/`** — the harness itself. Standard-library Python. This is
  the engine; everything else is a surface.
- **`integrations/vscode/extension/`** — the VS Code extension, the product
  surface. Chat, contract view, journal, skills, modes, providers. It talks
  to the harness through the Python sidecar and cannot bypass it.

## Run it

```sh
cd prototype && ./run_tests.sh   # full harness suite (621 tests, ~6 min; point TMPDIR at a roomy dir)
cd integrations/vscode/extension && npm test   # extension suite (node)
```

## Install the extension (beta)

Tagged prereleases on the
[releases page](https://github.com/Lukematic/harness-awino/releases) carry
the `.vsix`. Download it, then:

```sh
code --install-extension <downloaded-file>.vsix
```

or in VS Code: Extensions view → `…` → *Install from VSIX…*. No Python
setup needed — the runtime ships inside the package (0.5.x line).

## What's in the harness

| Piece | Where | What it does |
|---|---|---|
| Per-turn contract loop | `prototype/contract_loop.py`, `loop.py` | Compiles and validates the turn contract pre-turn and pre-execute; refuses broken contracts with named reasons |
| Modes (5) | `prototype/contract.py::MODES` | observe, plan, build, verify, ship — permission profiles, computed before the model acts |
| Stances (9) | `prototype/stances.py` | steel-man, feynman, planning-grill, first-principles, premortem, devil's-advocate, advisor, triage, verifier — reasoning procedures with rubrics |
| Skills (49) | `prototype/skills/` | Injected knowledge, SHA-256 pinned, verified at load. Core loop, rigor coach, osmani port, agent personas, references |
| Judge panel | `prototype/judges.py` | N judges vote by quorum per turn; no single gatekeeper; fail-closed |
| Skill synthesis | `prototype/synthesis.py` | Learnings → sandbox verification → pin → admit only on pass |
| Fan-out | `prototype/loop.py` | Parallel workers with atomic overlap/budget checks and a fail-closed synthesis barrier |
| Approval binding | `prototype/approvals.py` | Approval bound to canonical plan bytes, scope, base commit, approver, timestamp; re-approval chains via `supersedes_digest` |
| Discovery interview | `prototype/skills/discovery.md` | Fires on new tasks; grill enforces one-question-at-a-time, ask-XOR-advance |
| MCP server | `integrations/mcp-server/` | Standalone, client-agnostic: contract compiler, turn validator, judge panel, skill synthesis |

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

`main` carries the integrated harness (49 skills, 9 stances, fan-out,
judges, synthesis, adapter contract) and the 0.5.1 extension (bundled
Python, zero setup, truthfulness fixes — the beta-validated build).
Prerelease betas ship from tags via the release pipeline. Open gaps are
tracked at the bottom of `CAPABILITY_REGISTRY.md`.

Product name is still open — "Awino" appears throughout as the working name,
not the final brand.
