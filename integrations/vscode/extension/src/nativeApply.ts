/**
 * Native tool application: delegated file writes land through
 * vscode.workspace.applyEdit in ONE WorkspaceEdit (one undo unit) —
 * never through hand-rolled fs writes.
 *
 * Pre-image integrity: for each edit the sidecar sends old_digest (the
 * sha256 of the file as the loop saw it, null for new files). The
 * extension hashes the live document and refuses the edit when it
 * diverged (someone — the user, another tool — changed the file between
 * approval and application). A post-apply read-back echoes the digest so
 * the loop can verify byte-identity.
 *
 * This module needs the vscode API and is therefore compiled-not-run in
 * Node tests; the Windows GUI workflow proves it live
 * (NATIVE_APPLY_GUI_ASSERTIONS.md). Pure helpers live in
 * delegatedPure.ts and are unit-tested.
 */
import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import {
  sha256Hex,
  resolveDelegatedPath,
  DelegatedEditWire,
} from "./delegatedPure";

export interface ApplyResult {
  call_id: string;
  ok: boolean;
  result?: { path: string; digest: string; bytes: number };
  error?: string;
}

function guessLanguage(relPath: string): string | undefined {
  const ext = relPath.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    ts: "typescript",
    tsx: "typescriptreact",
    js: "javascript",
    jsx: "javascriptreact",
    json: "json",
    py: "python",
    md: "markdown",
    yaml: "yaml",
    yml: "yaml",
    sh: "shellscript",
    rs: "rust",
    go: "go",
    java: "java",
    c: "c",
    h: "c",
    cpp: "cpp",
    cs: "csharp",
    html: "html",
    css: "css",
    sql: "sql",
  };
  return map[ext];
}

/**
 * Apply a batch of delegated edits in a single WorkspaceEdit.
 * Every edit is validated and pre-image checked; a failed edit does not
 * block the others, and each outcome is reported per call_id.
 */
export async function applyDelegatedEdits(
  edits: DelegatedEditWire[],
  workspaceRoot: string
): Promise<ApplyResult[]> {
  const results: ApplyResult[] = [];
  const wsEdit = new vscode.WorkspaceEdit();
  // Edits that survive validation, in order; applied atomically below.
  const staged: {
    call_id: string;
    relPath: string;
    uri: vscode.Uri;
    content: string;
  }[] = [];

  for (const edit of edits) {
    const resolved = resolveDelegatedPath(workspaceRoot, edit);
    if ("error" in resolved) {
      results.push({
        call_id: String(edit.call_id ?? ""),
        ok: false,
        error: resolved.error,
      });
      continue;
    }
    const uri = vscode.Uri.file(resolved.absPath);
    let doc: vscode.TextDocument | null = null;
    try {
      doc = await vscode.workspace.openTextDocument(uri);
    } catch {
      doc = null; // not on disk (or not openable): treat as new file
    }
    if (doc === null) {
      // New file: the pre-image must be empty.
      if (edit.old_digest !== null && edit.old_digest !== undefined) {
        results.push({
          call_id: edit.call_id,
          ok: false,
          error: `pre-image changed: ${edit.path} was deleted after approval`,
        });
        continue;
      }
      wsEdit.createFile(uri, { ignoreIfExists: true });
      wsEdit.insert(uri, new vscode.Position(0, 0), edit.content);
      staged.push({
        call_id: edit.call_id,
        relPath: edit.path,
        uri,
        content: edit.content,
      });
      continue;
    }
    // Pre-image check against the LIVE document (what applyEdit will
    // replace), not just the bytes on disk: an open dirty editor holds
    // the truth.
    const liveDigest = sha256Hex(doc.getText());
    if (
      edit.old_digest !== null &&
      edit.old_digest !== undefined &&
      liveDigest !== edit.old_digest
    ) {
      results.push({
        call_id: edit.call_id,
        ok: false,
        error:
          `pre-image mismatch on ${edit.path}: the file changed after ` +
          `approval (expected ${String(edit.old_digest).slice(0, 12)}…, ` +
          `found ${liveDigest.slice(0, 12)}…). Edit refused; re-run to refresh.`,
      });
      continue;
    }
    const fullRange = new vscode.Range(0, 0, doc.lineCount, 0);
    wsEdit.replace(uri, fullRange, edit.content);
    staged.push({
      call_id: edit.call_id,
      relPath: edit.path,
      uri,
      content: edit.content,
    });
  }

  if (staged.length > 0) {
    let applied = false;
    try {
      applied = await vscode.workspace.applyEdit(wsEdit);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      for (const s of staged) {
        results.push({
          call_id: s.call_id,
          ok: false,
          error: `applyEdit threw: ${msg}`,
        });
      }
      return results;
    }
    if (!applied) {
      for (const s of staged) {
        results.push({
          call_id: s.call_id,
          ok: false,
          error: "workspace.applyEdit returned false (refused by VS Code)",
        });
      }
      return results;
    }
  }

  // Verify what actually landed and echo digests for the loop's check.
  // This is also the TOCTOU backstop: if the file changed between the
  // pre-image check and applyEdit, the digest won't match.
  for (const s of staged) {
    try {
      const bytes = fs.readFileSync(s.uri.fsPath, "utf8");
      const digest = sha256Hex(bytes);
      if (digest !== sha256Hex(s.content)) {
        results.push({
          call_id: s.call_id,
          ok: false,
          error: `post-apply digest mismatch on ${s.relPath}: disk differs from applied content`,
        });
        continue;
      }
      results.push({
        call_id: s.call_id,
        ok: true,
        result: { path: s.relPath, digest, bytes: s.content.length },
      });
    } catch (e) {
      results.push({
        call_id: s.call_id,
        ok: false,
        error: `could not verify ${s.relPath}: ${
          e instanceof Error ? e.message : String(e)
        }`,
      });
    }
  }
  return results;
}

/**
 * Open a native VS Code diff editor for an approval: current file (or an
 * empty document for new files) on the left, the proposed content on the
 * right. Used by the approval modal's "View diff" button and the chat
 * approval card's diff action — no hand-rolled HTML diff.
 */
export async function showApprovalDiff(
  relPath: string,
  proposedContent: string,
  oldExists: boolean,
  tool: string
): Promise<void> {
  const folders = vscode.workspace.workspaceFolders;
  const root = folders && folders[0] ? folders[0].uri.fsPath : "";
  const targetUri = vscode.Uri.file(
    root ? path.join(root, relPath) : relPath
  );
  let left: vscode.Uri;
  if (oldExists) {
    left = targetUri;
  } else {
    const empty = await vscode.workspace.openTextDocument({ content: "" });
    left = empty.uri;
  }
  const right = await vscode.workspace.openTextDocument({
    content: proposedContent,
    language: guessLanguage(relPath),
  });
  await vscode.commands.executeCommand(
    "vscode.diff",
    left,
    right.uri,
    `Awino proposal: ${tool} ${relPath}`,
    { preview: true }
  );
}
