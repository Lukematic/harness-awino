# Live mission run

`run_live.py` runs the agentic-learning literature-review mission against a
**real model**. It plays only the human: types the messages, approves the
contract and scope, clicks Approve. The model does the rest through the same
sidecar the VS Code extension runs. The report says what fired and what did
not; nothing is forced to pass.

Run it on GitHub (your key stays a secret):

1. Settings → Secrets and variables → Actions → add `AWINO_API_KEY`
   (OpenAI-compatible endpoint) or `ANTHROPIC_API_KEY`.
2. Actions → **live-mission** → Run workflow → provider, endpoint, model.
3. Open the run: the job summary shows the report; the **live-mission**
   artifact has `report.md`, `workspace/` (every file the model wrote,
   including `review.md` and the harness journal), `timeline.json`, and
   `video/agentic-learning-run.webm`.

Or locally: `AWINO_API_KEY=... python run_live.py --provider openai
--endpoint <url> --model <id> --out live-out`.

The scripted demo in `../demo-agentic-learning/` is not a live run: its
model replies were written by hand (it says so in its report and on every
video frame).
