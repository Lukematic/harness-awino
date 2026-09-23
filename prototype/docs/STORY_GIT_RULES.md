# Story git lifecycle rules

How git is used around the STORY.md story ledger. These rules are
implemented in `story.py` (`story_start`, `story_close`) and apply
everywhere — including Windows.

## The rules

1. **A branch per issue.** `story_start` creates (or reuses) a branch
   named `story/<slug>` from the story title. Branch creation is
   best-effort: if git is missing, the project isn't a repo, or the
   branch can't be created, the harness journals a warning and the
   mission continues. Never a failure.

2. **Commit early and often — locally.** During the work, commit to the
   story branch frequently. Local commits are cheap, offline, and the
   real safety net. Nothing is pushed until the story closes.

3. **Push on story close (best-effort).** `story_close` runs
   `git push -u origin <branch>`. If there is no remote, no
   credentials, or no network, the failure is journaled as a warning
   and the mission continues — closing the story never fails because
   the push failed. `GIT_TERMINAL_PROMPT=0` is set so a missing
   credential fails fast instead of hanging on a prompt.

4. **Merge via PR where `gh` exists (best-effort).** After a successful
   push, if the `gh` CLI is available, the harness opens a PR with
   `gh pr create --fill --base main --head <branch>` and journals the
   URL. The PR is never auto-merged — the human merges. If `gh` is
   missing or PR creation fails, that's a warning, not an error.

5. **Offline posture is never broken.** Every git step degrades to a
   journaled warning. A story can be started, worked, and closed with
   no network at all; the push simply waits for the next close (or a
   manual `git push`) when connectivity returns.

6. **Nothing is ever published.** Pushes go to the project's own
   private remote (`origin`) only. No public remotes, no releases, no
   tags — anything customer-facing still needs explicit human
   approval.

## WINDOWS-safe by construction

- All git invocations use argument-list `subprocess` calls — no shell,
  no shell quoting, no `&&` chains — so paths with spaces and
  non-POSIX shells behave.
- Every call has a timeout; nothing hangs the mission.
- No Unix-only assumptions (no `os.fork`, no signal tricks).

## Manual equivalents

```text
git checkout -b story/<slug>      # what story_start does
git add -A && git commit -m "..."  # early and often, locally
git push -u origin story/<slug>    # what story_close attempts
gh pr create --fill --base main --head story/<slug>
```
