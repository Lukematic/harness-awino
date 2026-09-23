# /awino — boot the A.W.I.N.O. loop-owner in this session

Adopt the loop-owner discipline for the rest of this session (see the
`awino-loop-owner` skill — read it first if you have not). Then:

1. If the user gave an objective with this command ($ARGUMENTS), call
   `awino_new_mission` with that objective text. Otherwise ask for the
   objective.
2. If the discovery interview opens (no criteria yet), run it: ONE material
   question per turn, no tool calls in question turns, working the frontier
   mission → primary user → goals → tenets → expectations → success metric.
   Never present a spec until it is resolved.
3. Once the mission is set, run the loop: every turn, `awino_compile_contract`
   first, draft the turn, `awino_validate_turn` before acting,
   `awino_judge_turn` after validation. Refuse to proceed on FAIL.
4. Walk DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP in order. Completion
   is computed from evidence (all done criteria `[x]`), never from claims.
5. Consequential actions pause for explicit user approval, bound to the exact
   arguments.

If the `awino` MCP server is not connected, say so up front and continue in
manual-discipline mode, telling the user that validation and judging are not
code-backed in this session.

Arguments: $ARGUMENTS
