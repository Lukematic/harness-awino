"use strict";
// test/cspPlaceholders.js — regression for the 0.5.0 webview outage.
//
// The chat and models webviews ship a restrictive CSP whose script-src uses
// a {{CSP_SOURCE}} placeholder, replaced at serve time with
// webview.cspSource. In 0.5.0 the placeholder ALSO appeared inside an HTML
// comment above the meta tag; String.replace with a string pattern only
// replaces the FIRST occurrence, so the comment ate the replacement and the
// real CSP directive kept the literal "{{CSP_SOURCE}}" — an invalid source.
// Browsers ignore the invalid source, the served CSP blocked the webview
// scripts' origin, and the chat webview went deaf: no state messages, no
// mission header, no turns (while tree views kept working).
//
// This test asserts the invariant the serve-time code relies on: after
// applying the extension's replacement, no placeholder survives in either
// HTML file, and the CSP meta tag carries the injected source.

const fs = require("fs");
const path = require("path");

const EXT_ROOT = path.resolve(__dirname, "..");

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log("ok   - " + name); }
  else { fail++; console.log("FAIL - " + name); }
}

// Mirror of the replacement in src/extension.ts (must stay global).
function serveHtml(html, cspSource) {
  return html.replace(/\{\{CSP_SOURCE\}\}/g, cspSource);
}

for (const file of ["webview/chat.html", "webview/models.html"]) {
  const p = path.join(EXT_ROOT, file);
  const html = fs.readFileSync(p, "utf8");
  const served = serveHtml(html, "https://file+.vscode-resource.vscode-cdn.net");

  ok(!served.includes("{{CSP_SOURCE}}"),
    `${file}: no {{CSP_SOURCE}} placeholder survives serve-time replacement`);
  ok(!served.includes("{{CSP_"),
    `${file}: no partial CSP placeholder survives`);
  ok(served.includes('script-src https://file+.vscode-resource.vscode-cdn.net'),
    `${file}: CSP meta tag carries the injected cspSource`);

  // Guard the original footgun directly: the raw file must not contain the
  // placeholder more than once (a non-global replace would silently leave
  // the later occurrences behind).
  const occurrences = (html.match(/\{\{CSP_SOURCE\}\}/g) || []).length;
  ok(occurrences <= 1,
    `${file}: placeholder occurs ${occurrences}x (<=1, so even a non-global replace is safe)`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
