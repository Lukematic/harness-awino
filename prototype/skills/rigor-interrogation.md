PROCEDURE rigor-interrogation (grill the plan before code; DEFINE phase):
1. TRIGGER when the distilled spec has Open Questions that block safe implementation, or the user asks to be grilled.
2. ASK ONE question at a time. Never a bulleted list of five. Wait for the answer before the next question.
3. PRESSURE-TEST along four axes: edge cases ("what happens if X fails?"), scope boundaries ("is Z required for v1 or out of scope?"), architecture ("are we prioritizing X or Y?"), failure modes ("how do we handle rate limits/timeouts here?").
4. After each answer: validate it briefly, then ask the next logical question. NO implementation code during interrogation.
5. EXIT when zero Open Questions remain on core behavior, or the user says stop. On exit, produce the final spec plus an Interrogation Record: table of (question, answer, ambiguity resolved), decisions captured (D-001...), and any remaining Open Questions.
6. NEVER fabricate answers to your own questions. An unanswered question stays open and blocks BUILD.

Attribution: adapted from agent-rigor 03_interrogation_protocol (MIT, MeherBhaskar).

Layered loading: the harness routes this skill only as noted below. Never bulk-load all rigor skills into one turn's context.
Routing: explicit skill_add only (deep-dive interviews); the DEFINE floor's discovery grill covers routine questioning.
