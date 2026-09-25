// chatHistory.ts — vscode-free transcript persistence for the chat webview.
//
// Why this exists: the chat webview keeps its transcript in DOM/JS memory
// only, and VS Code disposes a hidden sidebar WebviewView (e.g. the user
// switches to the Explorer tab to look at a folder). When the view
// re-resolves, the webview is re-created with an empty DOM — the whole
// session view is "wiped" even though the sidecar session is still alive.
//
// The extension host mirrors every transcript message here (bounded, so host
// memory stays flat) and replays the log into the webview on re-resolve.
// Chrome messages (connection state, provider pill, session-resume block)
// are NOT persisted: the view re-sends them fresh on every resolve because
// their renders are idempotent.

export const CHAT_HISTORY_MAX = 500;

export class ChatHistory {
  private items: unknown[] = [];

  /** Append a transcript message; evict oldest beyond the cap. */
  push(msg: unknown): void {
    this.items.push(msg);
    if (this.items.length > CHAT_HISTORY_MAX) {
      this.items.splice(0, this.items.length - CHAT_HISTORY_MAX);
    }
  }

  /** Replay the transcript in order through `post`. */
  replay(post: (msg: unknown) => void): void {
    for (const m of this.items) {
      post(m);
    }
  }

  get size(): number {
    return this.items.length;
  }

  /** Test seam: inspect the retained log. */
  entries(): unknown[] {
    return this.items.slice();
  }
}
