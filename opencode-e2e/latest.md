# opencode-e2e run 36486888473

- run: https://github.com/Lukematic/harness-awino/actions/runs/36486888473
- branch: ccr-672623e9-6zffvp  commit: abc63dfaec5e71414e92cd572aa589d4670af726
- fake-gateway scenarios: failure  real model: success

# A.W.I.N.O. OpenCode e2e

opencode 1.18.33

| scenario | result |
|---|---|
| 1-bearer-key | FAIL |
| 2-write-blocked-before-mission | FAIL |
| 3-write-allowed-after-set_mission | FAIL |
| 4-story_close-receipt-and-brag | FAIL |
| interviewer-cannot-edit | FAIL |
| unknown-a-stance-every-message | FAIL |
| unknown-b-redo-on-rule-break | FAIL |

## 1-bearer-key

- [x] run exits 0
- [ ] model was called
- [ ] every request carried Bearer test-key
- [ ] requests streamed
- [x] reply reached the CLI
- [ ] wrong key: gateway saw unauthenticated requests
- [x] wrong key: no reply reached the CLI

## 2-write-blocked-before-mission

- [ ] write tool was offered to the model
- [x] hi.txt NOT written
- [ ] block reason returned to the model
- [x] .awino/mission.json NOT forged via write

## 3-write-allowed-after-set_mission

- [ ] set_mission offered as a tool
- [ ] .awino/mission.json written by set_mission
- [ ] mission holds objective + done criteria
- [ ] hi.txt written after mission
- [x] run exits 0

## 4-story_close-receipt-and-brag

- [ ] story_close offered as a tool
- [ ] receipt .md written
- [ ] receipt has promise + proof
- [ ] brag board has the entry
- [x] direct write to BRAG.md denied
- [ ] second close appends (both entries present)

## interviewer-cannot-edit

- [ ] interviewer model calls happened
- [x] write/edit/patch not offered to interviewer
- [x] hi.txt NOT written by interviewer

## unknown-a-stance-every-message

- [ ] 'should we use postgres vs sqlite?': every call (0) carries stance steel-man
- [ ] 'teach me how does indexing work': every call (0) carries stance feynman
- [ ] 'fix the bug in notes': every call (0) carries stance first-principles
- [ ] tool round-trip turn made >1 model call

## unknown-b-redo-on-rule-break

- [ ] grill stance routed
- [ ] plugin sent a correction follow-up to the model
- [ ] final assistant reply is the redone one (one question)
- [x] no redo loop (<=2 corrections)

# A.W.I.N.O. OpenCode e2e

opencode 1.18.33

| scenario | result |
|---|---|
| 5-real-model | SKIPPED: REAL_API_KEY / REAL_MODEL not set (no repo secret) |

## 5-real-model

