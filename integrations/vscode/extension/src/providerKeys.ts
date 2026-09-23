// providerKeys.ts — vscode-free rule: which providers need an API key from
// VS Code SecretStorage, and whether that key is missing.
//
// echo and ollama need no key. Bedrock needs the Bedrock API key, Anthropic
// needs the Anthropic key, and everything else (openai plus any
// OpenAI-compatible custom provider) needs the AWINO_API_KEY. Keys live in
// SecretStorage only — never in settings JSON.

export interface KeyPresence {
  /** awino.apiKey.openai (AWINO_API_KEY) is stored */
  openai: boolean;
  /** awino.apiKey.anthropic (ANTHROPIC_API_KEY) is stored */
  anthropic: boolean;
  /** awino.apiKey.bedrock (Bedrock API key) is stored */
  bedrock: boolean;
}

/** True when `provider` needs a key that is not in SecretStorage. */
export function keyMissingForProvider(provider: string, keys: KeyPresence): boolean {
  switch ((provider ?? "").toLowerCase()) {
    case "echo":
    case "ollama":
      return false;
    case "scripted":
      // TEST ONLY provider: replays canned turns, no network, no key.
      // Claiming it needs one would show a lying setup card / wizard.
      return false;
    case "bedrock":
      return !keys.bedrock;
    case "anthropic":
      return !keys.anthropic;
    default:
      // "openai" and any OpenAI-compatible custom provider ride AWINO_API_KEY.
      return !keys.openai;
  }
}
