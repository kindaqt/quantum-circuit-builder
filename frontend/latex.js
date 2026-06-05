// ---- Minimal LaTeX (bra-ket) renderer --------------------------------------
// The professor explains with TeX math like
//   $|0\rangle \otimes \tfrac{1}{\sqrt{2}}(|0\rangle + |1\rangle)$
// The project has no build step and adds no dependencies, so rather than pull in
// KaTeX/MathJax this is a small hand-written renderer for the slice of TeX that
// actually shows up in quantum explanations: kets/bras, tensor products,
// fractions, square roots, super/subscripts, Greek letters, and the common
// relation/operator symbols. Anything it doesn't recognize degrades to plain
// text (the command name is shown verbatim) rather than throwing.

// Command name -> Unicode glyph. Covers the operators/relations/Greek that turn
// up in superposition/entanglement explanations.
const LATEX_SYMBOLS = {
  rangle: "⟩", langle: "⟨", otimes: "⊗", oplus: "⊕",
  cdot: "·", times: "×", div: "÷", pm: "±", mp: "∓",
  approx: "≈", neq: "≠", ne: "≠", leq: "≤", le: "≤",
  geq: "≥", ge: "≥", equiv: "≡", sim: "∼", propto: "∝",
  to: "→", rightarrow: "→", Rightarrow: "⇒", leftarrow: "←",
  Leftarrow: "⇐", leftrightarrow: "↔", mapsto: "↦",
  infty: "∞", partial: "∂", nabla: "∇", sum: "∑",
  prod: "∏", int: "∫", forall: "∀", exists: "∃",
  in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆",
  cup: "∪", cap: "∩", emptyset: "∅", dagger: "†",
  star: "⋆", ast: "∗", bullet: "∙", circ: "∘",
  angle: "∠", hbar: "ℏ", ell: "ℓ", Re: "ℜ", Im: "ℑ",
  ldots: "…", cdots: "⋯", dots: "…", vdots: "⋮",
  ddots: "⋱", langlebar: "⟨",
  // Greek (lower)
  alpha: "α", beta: "β", gamma: "γ", delta: "δ",
  epsilon: "ε", varepsilon: "ε", zeta: "ζ", eta: "η",
  theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ",
  lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π",
  rho: "ρ", sigma: "σ", tau: "τ", upsilon: "υ",
  phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  // Greek (upper)
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ",
  Xi: "Ξ", Pi: "Π", Sigma: "Σ", Upsilon: "Υ",
  Phi: "Φ", Psi: "Ψ", Omega: "Ω",
  // spacing
  quad: " ", qquad: "  ",
};

// HTML-escape a literal run that we control (math glyphs, plain text).
function texEscape(s) {
  return s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// Convert a TeX string to an HTML fragment. Recursive-descent over atoms:
// a brace group, a command, a super/subscript, or a single character.
function renderLatex(src) {
  const s = src;
  let i = 0;

  function parseSeq(stop) {
    let out = "";
    while (i < s.length && s[i] !== stop) out += parseAtom();
    return out;
  }

  function parseAtom() {
    const c = s[i];
    if (c === "{") {
      i++;
      const inner = parseSeq("}");
      if (s[i] === "}") i++;
      return inner;
    }
    if (c === "\\") return parseCommand();
    if (c === "^") { i++; return `<sup>${parseAtom()}</sup>`; }
    if (c === "_") { i++; return `<sub>${parseAtom()}</sub>`; }
    if (c === " ") { i++; return " "; }
    i++;
    return texEscape(c);
  }

  function parseCommand() {
    i++; // consume backslash
    // Control symbols: a backslash followed by a non-letter.
    if (i < s.length && !/[a-zA-Z]/.test(s[i])) {
      const c = s[i]; i++;
      if (c === "\\") return "<br>";
      if (c === ",") return " "; // thin space
      if (c === ";" || c === ":" || c === " ") return " ";
      if (c === "!") return "";       // negative thin space — just drop it
      return texEscape(c);            // \{ \} \% \& \# \_ etc. -> the literal char
    }
    // Control words: a backslash followed by letters.
    let name = "";
    while (i < s.length && /[a-zA-Z]/.test(s[i])) { name += s[i]; i++; }
    if (s[i] === " ") i++; // TeX swallows one space after a control word
    return applyCommand(name);
  }

  function applyCommand(name) {
    if (name === "frac" || name === "tfrac" || name === "dfrac") {
      const num = parseAtom();
      const den = parseAtom();
      return `<span class="tex-frac"><span class="tex-num">${num}</span>` +
             `<span class="tex-den">${den}</span></span>`;
    }
    if (name === "sqrt") {
      if (s[i] === "[") { while (i < s.length && s[i] !== "]") i++; if (s[i] === "]") i++; }
      return `<span class="tex-sqrt">√<span class="tex-sqrt-rad">${parseAtom()}</span></span>`;
    }
    if (name === "text" || name === "mathrm" || name === "operatorname") return parseAtom();
    if (name === "mathbf" || name === "boldsymbol") return `<b>${parseAtom()}</b>`;
    if (name === "mathit") return `<i>${parseAtom()}</i>`;
    if (name === "hat" || name === "widehat") return `<span class="tex-hat">${parseAtom()}</span>`;
    if (name === "bar" || name === "overline") return `<span class="tex-bar">${parseAtom()}</span>`;
    if (name === "vec") return `<span class="tex-vec">${parseAtom()}</span>`;
    if (name === "left" || name === "right") {
      // Render the delimiter that follows (\left( , \right| , \left. for none).
      if (i >= s.length) return "";
      if (s[i] === "\\") { i++; const d = s[i]; i++; return texEscape(d); }
      const d = s[i]; i++;
      return d === "." ? "" : texEscape(d);
    }
    if (Object.prototype.hasOwnProperty.call(LATEX_SYMBOLS, name)) return LATEX_SYMBOLS[name];
    // Unknown command: show its name so nothing silently vanishes.
    return texEscape(name);
  }

  return parseSeq(null);
}

// Wrap rendered math in a styled span (inline or centered display block).
function mathToHtml(tex, display) {
  return `<span class="math${display ? " math-display" : ""}">${renderLatex(tex.trim())}</span>`;
}

// Minimal inline Markdown for the professor's prose. The input is ALREADY
// HTML-escaped, so the only characters with special meaning left are the
// Markdown markers themselves (* _ `). Inline `code` is pulled out first so any
// * or _ inside it stays literal; the rest gets bold/italic emphasis. We don't
// attempt block constructs (headings, lists) — only the inline emphasis the
// professor actually emits, applied to non-math text so it never touches TeX.
function renderInlineMd(escaped) {
  return escaped.split(/(`[^`]+`)/g).map((seg) => {
    if (seg.length >= 2 && seg[0] === "`" && seg[seg.length - 1] === "`") {
      return `<code>${seg.slice(1, -1)}</code>`;
    }
    return seg
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")   // **bold**
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")        // __bold__
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")                // *italic*
      .replace(/(^|[^A-Za-z0-9])_([^_]+)_(?![A-Za-z0-9])/g, "$1<em>$2</em>"); // _italic_
  }).join("");
}

// Render a paragraph that may contain inline ($...$ or \(...\)) and display
// ($$...$$ or \[...\]) math, escaping the non-math text, applying inline
// Markdown emphasis to it, and turning newlines into <br>. A lone $ with no
// partner is treated as a literal character.
function renderRichText(p) {
  let out = "";
  let plain = "";
  let i = 0;
  const flush = () => { if (plain) { out += renderInlineMd(texEscape(plain)); plain = ""; } };
  const grab = (openLen, close) => {
    const end = p.indexOf(close, i + openLen);
    if (end === -1) return false;
    flush();
    out += mathToHtml(p.slice(i + openLen, end), close === "$$" || close === "\\]");
    i = end + close.length;
    return true;
  };
  while (i < p.length) {
    if (p.startsWith("$$", i) && grab(2, "$$")) continue;
    if (p.startsWith("\\[", i) && grab(2, "\\]")) continue;
    if (p.startsWith("\\(", i) && grab(2, "\\)")) continue;
    if (p[i] === "$" && grab(1, "$")) continue;
    plain += p[i];
    i++;
  }
  flush();
  return out.replace(/\n/g, "<br>");
}
