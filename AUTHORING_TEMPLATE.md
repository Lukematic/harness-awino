# A.W.I.N.O. Authoring Template

Use this to add a **stance**, **skill**, or **mode** without whack-a-mole.
Read `CAPABILITY_REGISTRY.md` first — if the capability already exists under
another name, extend it instead.

## Which one is it?

- **Mode** = permission profile (what the turn may touch). Add one only if a
  turn needs a tool set no existing mode grants.
- **Stance** = reasoning procedure + rubric (how the turn must think). Add one
  when a recurring situation needs enforced thinking, not just knowledge.
- **Skill** = injected knowledge/procedure body (what the turn may know). Add
  one when the model needs domain method it shouldn't have to fetch.

If it needs no rubric → it's a skill, not a stance.
If it changes no tool permissions → it's not a mode.

## Template: new STANCE

```python
# 1. stances.py :: STANCES["<kebab-name>"] = {
#        "procedure": ("PROCEDURE <name>:\n"
#                      "1. <step one>\n"
#                      "2. <step two>\n"
#                      "3. <step three>")}

# 2. Trigger — pick ONE:
#    a. INTENT_TABLE entry (checked in order; place BEFORE broader patterns
#       it must not fall through to):
#       ("<intent>", re.compile(r"<pattern>"),
#        "<mode>", ["<stance>"], ["<skill>", ...]),
#    b. FLOORS["<FLOOR>"]["stance"] default, or
#    c. chain position in an existing intent's stance list.

# 3. stances.py :: RUBRICS["<name>"] = [_rb_<name>_a, _rb_<name>_b, ...]
#    Each rubric: (turn, user_text) -> failure string | None.
#    REQUIRED: one rubric declaring tool discipline, e.g.
def _rb_<name>_tools(turn, user_text):
    if turn.get("tool_calls"):
        return "<name>: <why no tools / read-only here>"
    return None

# 4. Mode affinity: the intent's mode grants the tools; the stance only
#    restrains. State the discipline in one line:
#    "read-only; <what the stance may do with reads, and what it must not do>".
```

## Template: new SKILL

```python
# contract.py :: SKILLS["<kebab-name>"] = {
#     "name": "<kebab-name>",
#     "body": ("PROCEDURE <name>:\n"
#              "1. <step one>\n"
#              "2. <step two>\n"
#              "3. <step three>"),
# }
# Routed when: add "<kebab-name>" to the INTENT_TABLE entry's skill list
# and/or the FLOORS["<FLOOR>"]["skills"] list.
# Production: body loads from <path> with sha256 pin (done in Phase A via
# prototype/skills.py::SkillStore; add the file + manifest entry).
```

## Template: new MODE

```python
# contract.py :: MODES["<name>"] = {
#     "tools": [...],            # subset of TOOL_DEFS keys
#     "consequential": [...],    # subset needing human approval
#     "desc": "<one line: what the turn may touch>",
# }
# Routed when: INTENT_TABLE entry or FLOORS["<FLOOR>"]["mode"].
```

## Definition of done (every addition)

1. Registry row added to `CAPABILITY_REGISTRY.md`.
2. Routing test: input → expected (intent, mode, chain, skills).
3. Rubric pass test: good turn → ok.
4. Rubric fail test: bad turn → escalated, message names the rule.
5. Tool-discipline test: disallowed tool call → rejected before execution,
   no `tool_called` event.
6. Full suite green 5× (`python3 -m unittest discover -s tests`).

## Worked example: triage (2026-09-18)

Vague agent complaints ("you're not working", "it keeps misbehaving") used to
fall through to the fixer or die in planning-grill. Triage is a stance because
it needs enforced thinking (name the failure mode + state a falsifier), paired
with a skill carrying the failure-mode catalog.

- `STANCES["triage"]`: 4-step procedure (restate observed behavior →
  name the failure mode → state the falsifier → propose the smallest probe).
- Trigger: `("triage", re.compile(r"you'?re? (not working|broken|useless)|"
  r"agent is (bad|broken|not working)|misbehaving|acting weird|messed up"), "plan", ["triage"], ["triage", "domain"])`
  placed BEFORE `fix` so vague complaints don't become concrete repair jobs.
- Rubrics: named failure mode present; falsifier present; read-only
  (plan mode grants reads; no writes exist to misuse).
- `SKILLS["triage"]`: catalog of known failure modes
  (silent misroute, rubric false-positive, approval stall, scope drift,
  forged completion, tool outside mode) + naming guidance.
- Tests: `TestTriageStance` — routing, rubric pass/fail, write rejection.
