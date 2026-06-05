// Tests for the hand-written bra-ket LaTeX + inline-Markdown renderer (latex.js).
// These functions are pure string transforms (no DOM), and they back the
// Professor's answers, so correctness and graceful degradation both matter:
// unknown commands must show their name rather than throw, prose must be escaped,
// and a lone `$` must stay literal instead of opening a math span.
import test from "node:test";
import assert from "node:assert/strict";
import { loadFrontend } from "./harness.mjs";

function load() {
  return loadFrontend(["latex.js"]);
}

test("renderLatex maps ket/operator commands to Unicode glyphs", () => {
  const { run } = load();
  const html = run(String.raw`renderLatex("|0\\rangle \\otimes |1\\rangle")`);
  assert.match(html, /⟩/);
  assert.match(html, /⊗/);
});

test("renderLatex builds a fraction with numerator and denominator spans", () => {
  const { run } = load();
  const html = run(String.raw`renderLatex("\\tfrac{1}{2}")`);
  assert.match(html, /tex-frac/);
  assert.match(html, /tex-num/);
  assert.match(html, /tex-den/);
});

test("renderLatex renders a square root wrapper", () => {
  const { run } = load();
  const html = run(String.raw`renderLatex("\\sqrt{2}")`);
  assert.match(html, /tex-sqrt/);
  assert.match(html, /√/);
});

test("renderLatex degrades an unknown command to its name (no throw)", () => {
  const { run } = load();
  const html = run(String.raw`renderLatex("\\notacommand")`);
  assert.match(html, /notacommand/);
});

test("texEscape neutralizes HTML metacharacters", () => {
  const { run } = load();
  assert.equal(run(String.raw`texEscape("<b>&\"")`), "&lt;b&gt;&amp;&quot;");
});

test("renderInlineMd renders bold, italic, and inline code", () => {
  const { run } = load();
  assert.match(run('renderInlineMd("**bold**")'), /<strong>bold<\/strong>/);
  assert.match(run('renderInlineMd("*it*")'), /<em>it<\/em>/);
  assert.match(run('renderInlineMd("`code`")'), /<code>code<\/code>/);
});

test("renderInlineMd leaves snake_case identifiers untouched", () => {
  const { run } = load();
  // The underscore-italic rule requires word boundaries, so internal _ stays put.
  assert.equal(run('renderInlineMd("foo_bar_baz")'), "foo_bar_baz");
});

test("renderRichText renders inline math and escapes the surrounding prose", () => {
  const { run } = load();
  const html = run(String.raw`renderRichText("the state $|1\\rangle$ is <special>")`);
  assert.match(html, /class="math"/);   // the $...$ became a math span
  assert.match(html, /&lt;special&gt;/); // prose was HTML-escaped
});

test("renderRichText renders display math from $$...$$", () => {
  const { run } = load();
  const html = run(String.raw`renderRichText("$$\\tfrac{1}{2}$$")`);
  assert.match(html, /math-display/);
});

test("renderRichText treats a lone, unpaired $ as a literal (sad path)", () => {
  const { run } = load();
  const html = run('renderRichText("it costs $5 in total")');
  assert.match(html, /\$5/);
  assert.doesNotMatch(html, /class="math"/);
});
