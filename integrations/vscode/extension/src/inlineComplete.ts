/**
 * inlineComplete.ts — Tab ghost-text completions, Awino-style.
 *
 * This module is intentionally vscode-free: the pure logic (context
 * extraction, debounce) is exercised by plain node tests against the
 * compiled out/inlineComplete.js. VS Code API access arrives through the
 * injected `deps` of createInlineCompletionProvider().
 *
 * Differentiator: completions are mission-aware. The sidecar builds the
 * contract summary server-side from the live snapshot (phase, mode, done
 * criteria, skills) and instructs the model to return EMPTY rather than
 * suggest code that contradicts the mission phase. Empty = no ghost text,
 * which is the honest behavior — never a wrong completion.
 */

/** Hard caps: the completion prompt must stay tiny for latency. */
export const MAX_PREFIX_CHARS = 1500;
export const MAX_SUFFIX_CHARS = 750;

/** Languages the provider activates for (v1). */
export const SUPPORTED_LANGUAGES: ReadonlySet<string> = new Set([
  "typescript",
  "javascript",
  "typescriptreact",
  "javascriptreact",
  "python",
]);

export interface CompletionContext {
  /** Code before the cursor, capped at MAX_PREFIX_CHARS (tail kept). */
  prefix: string;
  /** Code after the cursor, capped at MAX_SUFFIX_CHARS. */
  suffix: string;
  /** File identity for the prompt (fs path or uri string). */
  file: string;
  /** VS Code language id. */
  language: string;
}

/**
 * Extract the completion request context from the document text.
 * Returns null when no completion should be attempted (unsupported
 * language, empty/whitespace prefix, cursor out of range).
 */
export function extractCompletionContext(
  fullText: string,
  offset: number,
  file: string,
  language: string
): CompletionContext | null {
  if (!SUPPORTED_LANGUAGES.has(language)) {
    return null;
  }
  if (offset <= 0 || offset > fullText.length) {
    return null;
  }
  const prefix = fullText.slice(Math.max(0, offset - MAX_PREFIX_CHARS), offset);
  const suffix = fullText.slice(offset, offset + MAX_SUFFIX_CHARS);
  if (!prefix.trim()) {
    return null;
  }
  return { prefix, suffix, file, language };
}

/**
 * Trailing-edge debouncer with generation tokens. Each trigger() bumps the
 * generation and resolves after `delayMs` of quiet; a caller that sees its
 * generation is no longer `current` must discard its work (a newer
 * keystroke arrived).
 */
export class KeystrokeDebouncer {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;

  /** Resolve with this call's generation after delayMs of no new trigger. */
  trigger(delayMs: number): Promise<number> {
    const gen = ++this.generation;
    if (this.timer) {
      clearTimeout(this.timer);
    }
    return new Promise((resolve) => {
      this.timer = setTimeout(() => {
        this.timer = null;
        resolve(gen);
      }, delayMs);
    });
  }

  get current(): number {
    return this.generation;
  }

  dispose(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

/** Smallest VS Code surface the provider factory needs (injected). */
export interface InlineCompletionHost {
  registerInlineCompletionItemProvider(
    selector: unknown,
    provider: {
      provideInlineCompletionItems(
        document: {
          getText(): string;
          offsetAt(position: { line: number; character: number }): number;
          uri: { fsPath?: string; toString(): string };
          languageId: string;
        },
        position: { line: number; character: number },
        context: unknown,
        token: {
          isCancellationRequested: boolean;
          onCancellationRequested(cb: () => void): unknown;
        }
      ): Promise<{ items: Array<{ insertText: unknown }> } | null>;
    }
  ): { dispose(): unknown };
  InlineCompletionItem: new (text: string) => any;
  InlineCompletionList: new (items: any[]) => any;
}

export interface InlineCompletionDeps {
  host: InlineCompletionHost;
  /** Send a `command` verb; resolves with the command_result payload. */
  query(
    name: string,
    args: Record<string, unknown>,
    timeoutMs: number
  ): Promise<unknown>;
  /** Live session state: false when disconnected (no ghost text ever). */
  isConnected: () => boolean;
  getConfig: () => { enable: boolean; debounceMs: number; model: string };
  log: (msg: string) => void;
}

const COMPLETE_TIMEOUT_MS = 8_000;
const COMPLETE_SELECTOR = [
  { language: "typescript" },
  { language: "javascript" },
  { language: "typescriptreact" },
  { language: "javascriptreact" },
  { language: "python" },
];

/**
 * Register the inline completion provider. The provider is registered once
 * and checks isConnected()/config on every keystroke, so no
 * connect/disconnect churn is needed. All failures resolve to an empty
 * list — typing must never break.
 */
export function createInlineCompletionProvider(deps: InlineCompletionDeps): {
  dispose(): void;
} {
  const debouncer = new KeystrokeDebouncer();

  const disposable = deps.host.registerInlineCompletionItemProvider(
    COMPLETE_SELECTOR,
    {
      async provideInlineCompletionItems(document, position, _context, token) {
        const empty = () => new deps.host.InlineCompletionList([]);
        let cfg: { enable: boolean; debounceMs: number; model: string };
        try {
          cfg = deps.getConfig();
        } catch {
          return empty();
        }
        if (!cfg.enable || !deps.isConnected()) {
          return empty();
        }
        let ctx: CompletionContext | null;
        try {
          const file =
            document.uri.fsPath ?? document.uri.toString();
          ctx = extractCompletionContext(
            document.getText(),
            document.offsetAt(position),
            file,
            document.languageId
          );
        } catch {
          return empty();
        }
        if (!ctx) {
          return empty();
        }
        // Debounce: wait for a typing pause. A newer keystroke supersedes this
        // call (its generation no longer matches `current`); VS Code also
        // cancels the token. Either way the stale call resolves to nothing
        // instead of dangling forever.
        const delayMs = Math.max(0, Math.min(cfg.debounceMs, 2000));
        const gen = await Promise.race([
          debouncer.trigger(delayMs),
          new Promise<null>((resolve) =>
            token.onCancellationRequested(() => resolve(null))
          ),
        ]);
        if (
          gen === null ||
          gen !== debouncer.current ||
          token.isCancellationRequested
        ) {
          return empty();
        }
        let result: unknown;
        try {
          const args: Record<string, unknown> = {
            prefix: ctx.prefix,
            suffix: ctx.suffix,
            file: ctx.file,
            language: ctx.language,
          };
          if (cfg.model && cfg.model.trim()) {
            args["model"] = cfg.model.trim();
          }
          result = await deps.query("complete", args, COMPLETE_TIMEOUT_MS);
        } catch (e) {
          deps.log(
            `inline completion query failed: ${
              e instanceof Error ? e.message : String(e)
            }`
          );
          return empty();
        }
        if (gen !== debouncer.current || token.isCancellationRequested) {
          return empty();
        }
        const completion =
          result && typeof result === "object"
            ? String(
                (result as Record<string, unknown>)["completion"] ?? ""
              )
            : "";
        if (!completion) {
          return empty(); // honest: no ghost text rather than a wrong one
        }
        return new deps.host.InlineCompletionList([
          new deps.host.InlineCompletionItem(completion),
        ]);
      },
    }
  );

  return {
    dispose(): void {
      debouncer.dispose();
      try {
        (disposable as { dispose(): unknown }).dispose();
      } catch {
        /* already gone */
      }
    },
  };
}
