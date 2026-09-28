# Getting started with Awino

Awino is a coding agent in VS Code that has to prove its work. It plans with
you, asks before it writes, runs your tests itself, and only calls a piece of
work done when there is evidence. Every finished story ends with a
**receipt** you can paste as a pull-request description.

## 1. Install

Download the `.vsix` from the
[releases page](https://github.com/Lukematic/harness-awino/releases), then in
VS Code: Extensions view → `…` → *Install from VSIX…*. The Python runtime is
bundled for Windows x64, macOS on Apple silicon and Linux x64. On an Intel Mac,
install Python 3.10+ first.

## 2. Connect a model (required)

Out of the box Awino runs on **Echo**, a demo provider that returns canned
replies so you can see the interface. It does no real work. To connect a real
model:

1. Open the chat (Awino icon in the activity bar). The setup card walks you
   through provider → key → model. Or run **Awino: Models & Providers**.
2. Anthropic or OpenAI: paste an API key (**Awino: Set API Key**). Keys are
   stored in VS Code's SecretStorage, never in settings files or logs.
   Ollama needs no key. For AWS, run **Awino: Set Up AWS Bedrock**.
3. Already set up in Claude Code, Kilo CLI or a project `.env`? **Awino:
   Import connections from other tools** brings the non-secret settings over
   (endpoint, model). You enter the key yourself; secrets are never copied.

Replies can be long: output is capped at 8,192 tokens by default. Set the
`AWINO_MAX_TOKENS` environment variable to change it.

## 3. Your first mission

1. Say what you want in the chat, e.g. *"add a --json flag to the export
   command"*. Awino asks a few questions to pin down the goal and what "done"
   means. It will push back if something looks like a bad idea.
2. It proposes a plan: the smallest version that works first (the "Honda"),
   plus a bigger idea pitched but not built (the "Bugatti"). Approve the plan
   and the files it may touch.
3. After you approve the plan, writes to those files and commands that stay
   in your workspace run without asking (autopilot). Each one is logged.
   Risky actions still show a card with the diff or command: deleting,
   `sudo`, force flags, `git push`/`reset`, installs, network tools, and
   anything outside the workspace. Choose **Approve**, **View diff**,
   **Always allow** (for this session) or **Deny**. To be asked every time,
   turn off the `awino.autoApproveAfterPlan` setting.
4. When code changes, Awino runs your project's `test` and `lint` recipes
   itself. A failure goes back to building, and a passing run goes to an
   independent verifier. The model's own "it works" is never enough.
5. When the work is verified, Awino asks whether to close the story.

## 4. Project setup, done for you

On first open Awino checks the project and offers the chores it can do:
- a `justfile` with `test`, `lint` and `format` for your language (Python,
  Node, Go, Rust);
- the missing `.gitignore` lines (`.venv/`, `node_modules/`, `.env`, …);
- a `.env.example` listing your `.env` keys, without the values;
- an `.editorconfig`.

It never writes these without a yes and never overwrites a file. Choose
**Review**, **Not now** or **Never**, or run **Awino: Set Up Project** later.

## 5. Stories, receipts and the brag board

Each piece of work is a **story**: a problem, a plan, done criteria and a
branch. The *Stories* panel lists them with time spent. Closing a story puts
it on the **brag board** (the task, the day it closed, the result and its
proof label) and produces a **receipt**:

- **Promise:** the done criteria and the planned steps, with time forecasts.
- **Proof:** commands and exit codes, the verifier's verdict, the files
  written, the commits, and the journal hash.
- **Lesson:** forecast vs actual time, failed checks, and anything closed
  without proof.

A receipt is labelled PROVEN, PARTLY PROVEN, SELF-CHECKED or UNVERIFIED. Use
**Copy as PR description** on the card, or **Awino: Show Story Receipt** on
any story.

## 6. Lessons

Receipts teach the next session. A step that ran 3× its forecast, or a test
that failed before passing, becomes a lesson. Awino shows it to the model on
every turn and lists it when you open a session. If the same problem comes back
after the lesson was shown, the lesson is escalated and Awino raises it with
you. After three clean stories it is marked learned and drops out.

## 6b. Models, tokens and tutoring

- **Three models (optional).** Set `awino.models.best` (planning, challenges,
  review), `awino.models.medium` (building from the approved plan) and
  `awino.models.basic` (single planned steps run by workers). Leave any of them
  blank to use the main model. If a step fails verification twice, the work
  moves up to the next model. Example on Anthropic: `claude-opus-5` /
  `claude-sonnet-5` / `claude-haiku-4-5`.
- **Token meter.** Under the chat: tokens used by this reply against its budget,
  the share read from the prompt cache, and the mission total. When a reply
  reaches `awino.turnTokenBudget` (default 60,000), it pauses and you say
  "continue". Skill text sits in the cached part of the prompt, so later
  rounds of a reply cost much less.
- **Tutor.** Say "tutor me in …", "help me learn … from scratch" or pick the
  Tutor mode. Awino places your level, gives a roadmap and the 80/20, then one
  challenge at a time: you try it, it reviews your attempt, names your biggest
  weakness and sets a harder challenge aimed at it.

## 7. Words you'll see

| Word | Meaning |
|---|---|
| Phase | Where the work is: Define → Plan → Build → Verify → Review → Ship. Each move has a gate. |
| Permission level | What Awino may do right now: observe (read only), plan, build (write), verify, ship. Chosen in code, never by the model. |
| Mode | A working style in the Modes view, such as Interview, Architect or Code. Each phase has a default. It never adds permissions. |
| Stance | How the model is reasoning this turn (e.g. steel-man, first principles, premortem). |
| Floor | A check that always runs in a phase, whatever stance the model chooses. |
| Skill | A pinned piece of know-how (e.g. TDD, debugging) added to the model's instructions. |
| Honda / Bugatti | The smallest version that works / the ambitious idea, pitched but not built. |
| Story | One piece of work: problem, plan, done criteria, branch. |
| Brag board | Closed stories, with date, outcome and time spent. |
| Receipt | Promise → proof → lesson for a closed story. |
| Lesson | Something a receipt taught, carried into later turns. |
| Approval | A card asking before any write or command with side effects. |

The full glossary and every command and setting are in the
[extension README](../integrations/vscode/extension/README.md).

## 8. Where your data lives

Everything is in `<project>/.awino/`: stories, receipts, lessons, tasks and
the session journal. The journal is hash-chained, so tampering shows. Session
journals stay out of git (`.awino/.gitignore`). `STORY.md` is generated, so
don't edit it by hand. API keys live only in VS Code SecretStorage.

## 9. If something goes wrong

| You see | Do this |
|---|---|
| Replies are canned or say "echo" | You're on the Echo demo provider. Connect a model (step 2). |
| "not connected" | Check the **Awino** output channel. If Python is missing, choose **Locate Python...** or set `awino.pythonPath`. |
| "HTTP 401" / "unauthorized" | The key is missing or wrong. Run **Awino: Set API Key**, then reconnect. |
| "The last session stopped in the middle of write_file …" | Check the file, then reply `applied` or `not applied` (not applied re-runs it). |
| "The session journal had a corrupt line …" | Awino kept everything before that line and saved the full file next to it. Nothing else is needed. |
| Verify keeps sending work back to Build | Your `test` or `lint` recipe is failing. The chat shows its output. |

## 10. Command line (optional)

```sh
pip install ./prototype        # installs the `awino` command
awino init                     # set up the current folder
awino status                   # mission, phase, next action
awino plan                     # task list: what's next, what's blocked
awino stories                  # stories and the brag board
awino rigor                    # rigor score for the latest mission
awino chat                     # interactive chat; /help lists commands
```

Without installing, run `python prototype/cli.py <command>` instead.

`awino init` is more eager than the extension. Besides `.awino/`, it creates
a `.venv`, a `justfile`, `README.md`, `lessons.md`, `spec/`, `docs/research/`
and `docs/archive/` if they are missing. Existing files are kept.

The command line talks to Echo (the default) or a local Ollama model:

```sh
AWINO_BACKEND=local OLLAMA_MODEL=qwen2.5:7b awino chat
```

`OLLAMA_HOST` sets the Ollama address. The default model is `qwen2.5:1.5b`.
Hosted providers (OpenAI-compatible, Anthropic, Bedrock) work only through
the VS Code extension.
