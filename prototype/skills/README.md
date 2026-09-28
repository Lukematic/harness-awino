# skills

The pinned skill store: 53 Markdown skill bodies that the harness injects
into the turn contract. The harness picks which skills a turn gets, from
the phase, stance and route. The model never fetches them.

| File | What it is |
|---|---|
| `*.md` | One skill each. The file name is the skill name. |
| `manifest.json` | Skill name → SHA-256 of the body. Checked at load; a mismatch stops the loop (`SkillIntegrityError`). |
| `layers.json` | Skill name → layer (`ceremony`, `mechanical`, `reference`) and route. Must list exactly the manifest's skills. |
| `network.json` | Network declarations. No entry means no network. |
| `__init__.py` | `SkillStore`: loads, hash-checks and serves the bodies. Line endings are normalized before hashing so Windows checkouts pass. |

Families: core loop (`code`, `verify`, `testing`, `discovery`, …), rigor
coach (`rigor-*`), osmani port (`osmani-*`), role lenses (`mode-*`),
references (`definition-of-done`, `critical-thinking`, …), plus
`durable-memory`, `debug`, `rpi` and `layered-loading`.

## Changing a skill

Editing a body changes its hash, so the store will refuse to load until
`manifest.json` is updated. Update the hash in the same commit, and the
counts in `tests/test_rigor.py` and `tests/test_memory_store.py` if you add
or remove a skill. The admission rules are in
[docs/SKILL_ADMISSION.md](../../docs/SKILL_ADMISSION.md) and the template in
[AUTHORING_TEMPLATE.md](../../AUTHORING_TEMPLATE.md).

Tests: `tests/test_skills.py`, `test_skill_security.py`,
`test_layered_loading.py`.

User-admitted skills (**Awino: Add Skill**, skill synthesis) go into a
separate project registry under `.awino/`, never into this folder.
