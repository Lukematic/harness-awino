/**
 * Fixture tests for AwinoMarkdown (webview/chat.js, spec §4.2–§4.4).
 *
 * Byte-exact HTML assertions covering: paragraphs, ATX headings (incl. the
 * h5 cap), **bold**, *italic*, `inline code`, fenced code blocks (with lang,
 * without lang, unclosed), ```diff rendering, unordered/ordered/2-level
 * nested lists, https/http links, javascript:/ftp link rejection,
 * blockquotes, hr, pipe-table fallback to a code block, and XSS fixtures
 * (<script>, <img onerror>) which must render fully escaped.
 *
 * Run: node test/markdown.js   (from integrations/vscode/extension)
 */
"use strict";
const assert = require("assert");
const { AwinoMarkdown } = require("../webview/chat.js");

const COPY = "⧉ Copy"; // \u29C9, the copy-button label per spec §4.3

let passed = 0;
let failed = 0;

function codeblock(langLabel, body) {
  return '<div class="codeblock"><div class="codeblock-header">' +
    '<span class="codeblock-lang">' + langLabel + "</span>" +
    '<button type="button" class="copy-btn">' + COPY + "</button></div>" +
    '<pre class="codeblock-body" id="awino-cb-1">' + body + "</pre></div>";
}

function t(name, input, expected) {
  AwinoMarkdown._resetIds(); // deterministic awino-cb-N ids per fixture
  let actual;
  try {
    actual = AwinoMarkdown(input);
  } catch (e) {
    failed++;
    console.log("FAIL - " + name + "  (threw: " + e.message + ")");
    return;
  }
  try {
    assert.strictEqual(actual, expected);
    passed++;
    console.log("ok   - " + name);
  } catch (e) {
    failed++;
    console.log("FAIL - " + name);
    console.log("  input:    " + JSON.stringify(input));
    console.log("  expected: " + JSON.stringify(expected));
    console.log("  actual:   " + JSON.stringify(actual));
  }
}

/* ---------- paragraphs ---------- */
t("paragraph", "hello world", "<p>hello world</p>");
t("paragraph-joins-lines", "line one\nline two", "<p>line one line two</p>");
t("paragraphs-split-on-blank", "a\n\nb", "<p>a</p><p>b</p>");
t("empty-input", "", "");
t("crlf-normalized", "a\r\n\r\nb", "<p>a</p><p>b</p>");
t("ampersand-escaped", "a & b", "<p>a &amp; b</p>");

/* ---------- headings ---------- */
t("h1-h3", "# Title", "<h3>Title</h3>");
t("h2-h4", "## Section", "<h4>Section</h4>");
t("h3-h5", "### Sub", "<h5>Sub</h5>");
t("h4-capped-at-h5", "#### Deep", "<h5>Deep</h5>");
t("h6-capped-at-h5", "###### Deeper", "<h5>Deeper</h5>");
t("heading-inline-markup", "## **Bold** head", "<h4><strong>Bold</strong> head</h4>");

/* ---------- inline markup ---------- */
t("bold", "**bold**", "<p><strong>bold</strong></p>");
t("italic", "*italic*", "<p><em>italic</em></p>");
t("bold-and-italic", "**bold** and *italic*", "<p><strong>bold</strong> and <em>italic</em></p>");
t("inline-code", "use `x()` now", "<p>use <code>x()</code> now</p>");
t("inline-code-escapes-html", "`<div>`", "<p><code>&lt;div&gt;</code></p>");
t("inline-code-protects-stars", "`*not italic*`", "<p><code>*not italic*</code></p>");

/* ---------- fenced code blocks ---------- */
t("fenced-with-lang",
  "```python\nprint(1)\n```",
  codeblock("python", "print(1)"));
t("fenced-no-lang-defaults-to-text",
  "```\nplain\n```",
  codeblock("text", "plain"));
t("fenced-escapes-html",
  "```\n<script>alert(1)</script>\n```",
  codeblock("text", "&lt;script&gt;alert(1)&lt;/script&gt;"));
t("fenced-unclosed",
  "```js\nfoo(",
  codeblock("js", "foo("));
t("fenced-lang-case-insensitive-diff",
  "```DIFF\n+ a\n```",
  codeblock("DIFF", '<span class="diff-add"><span class="dm">+</span> a</span>'));

/* ---------- diff rendering (§4.4) ---------- */
t("diff-block",
  "```diff\n@@ -1,2 +1,2 @@\n- old line\n+ new line\n  context\n+++ b/file\n--- a/file\n```",
  codeblock("diff",
    '<span class="diff-hunk">@@ -1,2 +1,2 @@</span>' +
    '<span class="diff-del"><span class="dm">-</span> old line</span>' +
    '<span class="diff-add"><span class="dm">+</span> new line</span>' +
    '<span class="diff-ctx">  context</span>' +
    '<span class="diff-ctx">+++ b/file</span>' +
    '<span class="diff-ctx">--- a/file</span>'));
t("diff-escapes-html",
  "```diff\n+ <b>x</b>\n```",
  codeblock("diff",
    '<span class="diff-add"><span class="dm">+</span> &lt;b&gt;x&lt;/b&gt;</span>'));

/* ---------- lists ---------- */
t("unordered-list",
  "- a\n- b",
  "<ul><li>a</li><li>b</li></ul>");
t("unordered-list-star-marker",
  "* a\n* b",
  "<ul><li>a</li><li>b</li></ul>");
t("ordered-list",
  "1. a\n2. b",
  "<ol><li>a</li><li>b</li></ol>");
t("ordered-list-paren-marker",
  "1) a\n2) b",
  "<ol><li>a</li><li>b</li></ol>");
t("nested-list-2-level",
  "- a\n  - b\n- c",
  "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>");
t("nested-mixed-list",
  "- a\n  1. b\n- c",
  "<ul><li>a<ol><li>b</li></ol></li><li>c</li></ul>");
t("list-inline-markup",
  "- **a** and `b`",
  "<ul><li><strong>a</strong> and <code>b</code></li></ul>");

/* ---------- links ---------- */
t("link-https",
  "[docs](https://example.com)",
  '<p><a href="https://example.com" target="_blank" rel="noopener">docs</a></p>');
t("link-http",
  "[h](http://example.com/x)",
  '<p><a href="http://example.com/x" target="_blank" rel="noopener">h</a></p>');
t("link-javascript-rejected",
  "[x](javascript:alert(1))",
  "<p>[x](javascript:alert(1))</p>");
t("link-ftp-rejected",
  "[x](ftp://example.com)",
  "<p>[x](ftp://example.com)</p>");
t("link-quote-in-url-rejected",
  '[x](https://example.com/"onmouseover="alert(1))',
  '<p>[x](https://example.com/"onmouseover="alert(1))</p>');

/* ---------- blockquotes / hr ---------- */
t("blockquote",
  "> hello",
  "<blockquote><p>hello</p></blockquote>");
t("blockquote-multi-line",
  "> a\n> b",
  "<blockquote><p>a b</p></blockquote>");
t("blockquote-inline-markup",
  "> **note** `x`",
  "<blockquote><p><strong>note</strong> <code>x</code></p></blockquote>");
t("hr", "---", "<hr>");

/* ---------- tables are not supported -> code block ---------- */
t("table-fallback",
  "| a | b |\n|---|---|\n| 1 | 2 |",
  codeblock("table", "| a | b |\n|---|---|\n| 1 | 2 |"));
t("table-fallback-aligned",
  "| a | b |\n|:---|---:|\n| 1 | 2 |",
  codeblock("table", "| a | b |\n|:---|---:|\n| 1 | 2 |"));

/* ---------- XSS fixtures: must render fully escaped ---------- */
t("xss-script",
  "<script>alert('x')</script>",
  "<p>&lt;script&gt;alert('x')&lt;/script&gt;</p>");
t("xss-img-onerror",
  '<img src=x onerror=alert(1)>',
  "<p>&lt;img src=x onerror=alert(1)&gt;</p>");
t("xss-in-bold",
  "**<img src=x onerror=alert(1)>**",
  "<p><strong>&lt;img src=x onerror=alert(1)&gt;</strong></p>");
t("xss-in-codeblock",
  "```html\n<img src=x onerror=alert(1)>\n```",
  codeblock("html", "&lt;img src=x onerror=alert(1)&gt;"));

/* ---------- composite ---------- */
t("composite-doc",
  "# Plan\n\nDo **this** then *that*. See [docs](https://example.com).\n\n- one\n- two",
  "<h3>Plan</h3>" +
  '<p>Do <strong>this</strong> then <em>that</em>. See ' +
  '<a href="https://example.com" target="_blank" rel="noopener">docs</a>.</p>' +
  "<ul><li>one</li><li>two</li></ul>");

/* ---------- API surface ---------- */
t("window-and-module-exports",
  "x", "<p>x</p>"); // require() path already proved the module export works
if (typeof AwinoMarkdown.wireCopyButtons !== "function") {
  failed++;
  console.log("FAIL - wireCopyButtons is not a function");
} else {
  passed++;
  console.log("ok   - wireCopyButtons exposed");
}

console.log("\n" + passed + " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
