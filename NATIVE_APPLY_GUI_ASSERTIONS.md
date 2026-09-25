# NATIVE_APPLY_GUI_ASSERTIONS.md

Windows GUI assertions for the native tool-application workstream
(`feature/native-tool-application`). These cover the vscode-dependent half
that cannot run headless: `WorkspaceEdit` application, `vscode.diff` approval
review, and integrated-terminal shell execution. The protocol half (sidecar
↔ extension message shapes, batching, digests, checkpoint/revert) is proven
by `test/delegatedApply.js` and `prototype/tests/test_delegated_apply.py`.

Run these against a Windows VS Code instance with the extension installed
from the locally-built VSIX, a scripted sidecar driving multi-file writes,
and the operator watching.

## A1 — Native multi-file apply is one undo unit

**Setup:** Scripted turn proposes `write_file` to `a.txt` and `patch_file`
to `b.txt` (both consequential → approval modal).

**Expected:**
- The approval modal appears for each file (Approve / Deny / View diff).
- After approving both, BOTH files change in the workspace.
- Pressing Ctrl+Z ONCE reverts BOTH files (single undo unit).

**Specifically why:** The sidecar drains only when the approval round is
complete and sends ONE `apply_requested` with both edits; the extension
builds ONE `WorkspaceEdit` and calls `workspace.applyEdit` once. Two
separate `applyEdit` calls would produce two undo steps — the failure mode
this assertion guards against.

## A2 — Native diff editor for patch review

**Setup:** Scripted turn proposes `patch_file` with a unified diff.

**Expected:**
- The approval modal (and the chat approval card) offer **View diff**.
- Clicking it opens a `vscode.diff` editor: left = current file content,
  right = proposed content (from the sidecar's `proposed_content`).
- The diff editor is read-only preview; closing it returns to the modal.
- Approve/Deny authority is unchanged — the diff is review-only.

**Specifically why:** Patch approvals carry `proposed_content` computed by
the sidecar (strict Python diff parsing). The extension must not recompute
the patch — it displays what the sidecar will write.

## A3 — Live terminal streaming

**Setup:** Scripted turn proposes `run_command` with `echo hello` (or a
command with slow output, e.g. `ping -n 5 127.0.0.1`).

**Expected:**
- After approval, an integrated terminal appears (or reuses the Awino
  terminal) and the command executes.
- Output streams LIVE into the chat (not buffered until completion).
- The turn completes with the streamed stdout in the tool result.

**Specifically why:** The extension forwards `terminal_output` chunks as
they arrive via shell-integration `execution.read()`; the sidecar
accumulates them and journals the final stdout with the exit code.

## A4 — Long-running process and cancellation

**Setup:** Scripted turn proposes `run_command` with a long sleep
(`timeout /t 30` or `ping -n 30 127.0.0.1`).

**Expected:**
- While running, the operator can cancel (the Cancel button / command).
- Cancellation sends `terminal_kill`; the terminal process is disposed.
- The turn journals `killed: true` with `reason: "killed"`.
- A command that exceeds its timeout journals `timed_out: true` with
  `reason: "timed_out"`.

**Specifically why:** The sidecar's wait loop defers the `cancel` command,
emits `terminal_kill`, and keeps waiting for `terminal_result` so the run
always journals a final state — a dropped kill must not leave the turn
hanging.

## A5 — Checkpoint / revert

**Setup:** Make an uncommitted edit to a tracked file yourself (operator's
own work). Then run a scripted turn that writes two files (approved).

**Expected:**
- After the turn, `awino.revertCheckpoint` (Command Palette) restores the
  workspace.
- The operator's own uncommitted edit is STILL PRESENT after revert.
- Files Awino created are deleted; files Awino modified return to their
  pre-turn content.

**Specifically why:** The checkpoint stashes the workspace (excluding the
sidecar's own `.awino/` state dir) BEFORE the delegated batch. Revert does
`reset --hard HEAD`, deletes only Awino-created untracked files, then
`stash apply`. The operator's work must survive — the checkpoint is taken
before Awino's writes, not after.

## A6 — Digest mismatch blocks the write

**Setup:** (Simulated) A test hook or manual interception modifies the file
between the sidecar computing the write and the extension applying it.

**Expected:**
- The extension refuses to apply (pre-image digest mismatch).
- The turn journals an error; no partial write occurs.
- The operator sees which file failed and why.

**Specifically why:** Every delegated edit carries `old_digest` (pre-image)
and the expected `digest`. The extension validates the pre-image before
applying and hashes what it wrote before answering. A mismatch means the
file changed under us — applying would clobber the operator's edit.

## A7 — Terminal failure reasons are honest

**Setup:** (If reproducible) Close the terminal mid-run; run on a machine
where shell integration is unavailable.

**Expected:**
- `terminal_result` carries a `reason`: `completed` | `timed_out` |
  `killed` | `terminal_closed` | `shell_integration_unavailable` |
  `execute_failed`.
- The chat shows the honest reason, not a silent `exitCode: undefined`.

**Specifically why:** The runner previously returned `exitCode: undefined,
timedOut: false, killed: false` for all failure modes — indistinguishable.
The `reason` field makes each terminal state explicit in the journal.
