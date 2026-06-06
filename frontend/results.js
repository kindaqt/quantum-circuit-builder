// ---- Results & export ------------------------------------------------------
// Running the circuit and showing what came back: the live/quantum /simulate
// dispatch, the measurement histogram (+ PNG/CSV export), the statevector and
// Bloch panels, the results tabs and Run-mode control, and the Export modal that
// renders the circuit as Qiskit/OpenQASM plus a client-side circuit PNG.

// ---- Simulation ------------------------------------------------------------
let simTimer = null;
function scheduleSim() {
  clearTimeout(simTimer);
  // Never auto-fire a real-hardware job on every edit — that would queue/cost a
  // QPU run per keystroke. Quantum runs are triggered explicitly by the button.
  if (state.runMode === "quantum") {
    setRunStatus("Circuit changed — press Run", "");
    return;
  }
  simTimer = setTimeout(() => runSim("sim"), 120);
}

// Run the local classical simulation. This fires constantly as the circuit is
// edited (live sim) to keep the result panels current — that path must NOT log to
// the queue. Only an explicit Run-button press (`toQueue: true`) snapshots the
// circuit + its result into the Queue tab. Quantum mode always goes through the
// queue (see submitQuantumRun).
async function runSim(mode = state.runMode, { toQueue = false } = {}) {
  if (mode === "quantum") return submitQuantumRun();
  const payload = {
    num_qubits: state.numQubits,
    shots: state.shots,
    mode,
    gates: gatePayload(),
  };
  // Snapshot the circuit (picture + signature) at request time, so the queue card
  // reflects exactly what ran even if the canvas is edited before the reply lands.
  const snap = circuitSVG();
  const sig = circuitSig();
  try {
    const res = await fetch("/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      $("histogram").innerHTML = `<div class="hint">${err.detail || "Run rejected."}</div>`;
      // An explicit Run press deserves a loud error (the live sim stays quiet in
      // the histogram so we don't pop a modal on every keystroke).
      if (toQueue) showError(err.detail || "The simulator rejected this circuit.", "Run failed");
      return;
    }
    const data = await res.json();
    renderResults(data);
    // Trigger auto-explain whenever a simulation completes (the sig-dedup inside
    // maybeAutoExplain() ensures we only explain circuits that actually changed).
    maybeAutoExplain();
    // Only an explicit Run press records the circuit + result in the queue.
    if (toQueue) {
      recordClassicalRun(snap, payload.num_qubits, payload.shots, sig, data);
      activateTab("queue"); // surface the queue so the user sees their logged run
    }
  } catch (err) {
    $("histogram").innerHTML = `<div class="hint">Run error: ${err}</div>`;
    if (toQueue) showError("Couldn't reach the simulator: " + err, "Run failed");
  }
}

function renderResults(data) {
  renderHistogram(data.counts);
  renderStatevector(data.statevector);
  renderBloch(data.bloch);
  // On hardware/qsim runs the amplitudes and Bloch vectors can't come from the
  // device (measurement yields counts only). The backend fills them from a local
  // simulation of the same circuit; flag that here so we never imply otherwise.
  toggleSimNote(data.extras_simulated);
}

// Prepend (or remove) a "these are simulated, not measured" banner on the
// Statevector and Bloch panels.
function toggleSimNote(simulated) {
  const msg =
    "Simulated, not measured. Real quantum hardware only returns measurement counts, " +
    "so these amplitudes and Bloch vectors are computed from a local simulation of the " +
    "same circuit — the ideal, noise-free result.";
  for (const id of ["statevector", "bloch"]) {
    const panel = $(id);
    const existing = panel.querySelector(".sim-note");
    if (existing) existing.remove();
    if (simulated) {
      const note = document.createElement("div");
      note.className = "sim-note";
      note.textContent = msg;
      panel.prepend(note);
    }
  }
}

// ---- Histogram -------------------------------------------------------------
// How the measurement histogram is displayed. `mode` toggles raw shot counts vs.
// probabilities (counts / total shots); `sort` toggles basis order vs. tallest
// bar first. Both are pure view state — they never re-run the circuit.
const histView = { mode: "counts", sort: "basis" };
let lastCounts = null;

// Ordered [basis, count] pairs honouring the current sort. Basis order is the
// natural ascending bitstring order; "by value" is descending count, ties broken
// by basis so the layout stays stable across equal bars.
function histOrderedEntries(counts) {
  const entries = Object.entries(counts);
  if (histView.sort === "value") {
    entries.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  } else {
    entries.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  }
  return entries;
}

function renderHistogram(counts) {
  lastCounts = counts;
  const el = $("histogram");
  el.innerHTML = "";
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  const max = Math.max(1, ...Object.values(counts));
  for (const [k, c] of histOrderedEntries(counts)) {
    // Bar heights are identical either way (probability is just count/total), so
    // only the printed value changes with the mode.
    const value = histView.mode === "prob" ? `${((c / total) * 100).toFixed(1)}%` : `${c}`;
    const wrap = document.createElement("div");
    wrap.className = "hbar";
    wrap.innerHTML =
      `<div class="cnt">${value}</div>` +
      `<div class="bar" style="height:${(c / max) * 100}%"></div>` +
      `<div class="lbl">|${k}⟩</div>`;
    el.appendChild(wrap);
  }
}

// Re-paint the histogram from the last results when a view toggle changes, with
// no new simulation. No-op until the first run has produced counts.
function refreshHistogram() {
  if (lastCounts) renderHistogram(lastCounts);
}

// Highlight the active button within a segmented control and apply the change.
function setHistOption(group, btn) {
  if (group === "mode") histView.mode = btn.dataset.histMode;
  else histView.sort = btn.dataset.histSort;
  document.querySelectorAll(`[data-hist-${group === "mode" ? "mode" : "sort"}]`).forEach((b) =>
    b.classList.toggle("active", b === btn));
  refreshHistogram();
}

// Standalone SVG of the measurement histogram (for PNG export), drawn in the same
// order/mode the user is currently viewing.
function histogramSVG() {
  const C = SVG_COLORS;
  const entries = histOrderedEntries(lastCounts || {});
  const total = entries.reduce((a, [, c]) => a + c, 0) || 1;
  const max = Math.max(1, ...entries.map(([, c]) => c));
  const barW = 38, gap = 16, padL = 16, padR = 16, padTop = 24, barH = 200, labelH = 46;
  const W = padL + padR + entries.length * barW + Math.max(0, entries.length - 1) * gap;
  const H = padTop + barH + labelH;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${Math.max(W, 120)}" height="${H}" ` +
    `viewBox="0 0 ${Math.max(W, 120)} ${H}" ` +
    `font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">`;
  s += `<rect width="${Math.max(W, 120)}" height="${H}" fill="${C.panel}"/>`;
  entries.forEach(([k, c], i) => {
    const x = padL + i * (barW + gap);
    const h = Math.round((c / max) * barH);
    const y = padTop + barH - h;
    const value = histView.mode === "prob" ? `${((c / total) * 100).toFixed(1)}%` : `${c}`;
    s += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" rx="3" fill="${C.gate}"/>`;
    s += `<text x="${x + barW / 2}" y="${y - 6}" fill="${C.text}" font-size="11" text-anchor="middle">${svgEsc(value)}</text>`;
    s += `<text x="${x + barW / 2}" y="${padTop + barH + 16}" fill="${C.muted}" font-size="11" text-anchor="middle">|${svgEsc(k)}⟩</text>`;
  });
  s += `</svg>`;
  return { svg: s, W: Math.max(W, 120), H };
}

async function downloadHistogramPng() {
  if (!lastCounts) return;
  const { svg, W, H } = histogramSVG();
  try {
    const { blob } = await svgToPng(svg, W, H, 2);
    downloadBlob(blob, "histogram.png");
  } catch (_) { /* rasterization unavailable — silently skip */ }
}

function downloadHistogramCsv() {
  if (!lastCounts) return;
  const total = Object.values(lastCounts).reduce((a, b) => a + b, 0) || 1;
  const rows = [["basis", "count", "probability"]];
  for (const [k, c] of histOrderedEntries(lastCounts)) {
    rows.push([k, String(c), (c / total).toFixed(6)]);
  }
  const csv = rows.map((r) => r.join(",")).join("\n") + "\n";
  downloadBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "results.csv");
}

// Trigger a browser download of a Blob under the given filename.
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- Statevector & Bloch ---------------------------------------------------
function renderStatevector(sv) {
  let rows = sv
    .map(
      (s) =>
        `<tr><td class="mono">|${s.basis}⟩</td>` +
        `<td class="mono">${s.re.toFixed(3)} ${s.im >= 0 ? "+" : "−"} ${Math.abs(s.im).toFixed(3)}i</td>` +
        `<td class="mono">${s.prob.toFixed(3)}</td>` +
        `<td class="mono">${s.phase.toFixed(1)}°</td></tr>`
    )
    .join("");
  $("statevector").innerHTML =
    `<table><thead><tr><th>Basis</th><th>Amplitude</th><th>Probability</th><th>Phase</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderBloch(bloch) {
  const el = $("bloch");
  el.innerHTML = "";
  const R = 50, C = 60;
  bloch.forEach((v, q) => {
    const [x, y, z] = v;
    const r = Math.sqrt(x * x + y * y + z * z);
    const px = C + x * R, py = C - z * R;
    const mixed = r < 0.05;
    const cell = document.createElement("div");
    cell.className = "bloch-cell";
    cell.innerHTML =
      `<div class="name">qubit ${q}</div>` +
      `<svg width="120" height="120">` +
      `<circle cx="${C}" cy="${C}" r="${R}" fill="none" stroke="#3a4150"/>` +
      `<ellipse cx="${C}" cy="${C}" rx="${R}" ry="16" fill="none" stroke="#2a2f3a"/>` +
      `<line x1="${C}" y1="${C - R}" x2="${C}" y2="${C + R}" stroke="#2a2f3a"/>` +
      `<line x1="${C - R}" y1="${C}" x2="${C + R}" y2="${C}" stroke="#2a2f3a"/>` +
      (mixed
        ? `<circle cx="${C}" cy="${C}" r="4" fill="#8b93a3"/>`
        : `<line x1="${C}" y1="${C}" x2="${px}" y2="${py}" stroke="#4b8bff" stroke-width="2"/>` +
          `<circle cx="${px}" cy="${py}" r="4" fill="#4b8bff"/>`) +
      `</svg>` +
      `<div class="nums">${mixed ? "mixed (r≈0)" : `x ${x.toFixed(2)}  y ${y.toFixed(2)}  z ${z.toFixed(2)}`}</div>`;
    el.appendChild(cell);
  });
}

// ---- Tabs ------------------------------------------------------------------
function activateTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  const panel = $("panel-" + name);
  if (panel) panel.classList.add("active");
}

// ---- Run mode (classical simulator vs IBM quantum hardware) ----------------
function setRunStatus(text, kind) {
  const el = $("run-status");
  if (!el) return;
  el.textContent = text;
  el.className = "run-status" + (kind ? " " + kind : "");
}

function setRunMode(mode) {
  state.runMode = mode;
  // The Run button is available in both modes: in quantum mode it submits a
  // hardware job; in classical mode it snapshots the live result into the queue.
  $("run-hw").hidden = false;
  if (mode === "quantum") {
    clearTimeout(simTimer); // cancel any pending live-sim so it can't overwrite results
    setRunStatus("Press Run to submit", "");
  } else {
    setRunStatus("", "");
    scheduleSim(); // resume live local simulation
  }
  updateRunAvailability(); // reflect mode + any in-flight runs against the queue cap
}

// ---- Export (Qiskit + OpenQASM) --------------------------------------------
// Ask the backend to render the current circuit as runnable Qiskit Python and
// OpenQASM 3, and show both in a modal with copy buttons. The backend validates
// the same way every other entry point does, so nothing here weakens the gate
// whitelist or the bounds.
async function openExport() {
  const modal = $("export-modal");
  $("export-qiskit").textContent = "Generating…";
  $("export-qasm").textContent = "";
  modal.classList.remove("hidden");
  renderCircuitDiagram();  // client-side PNG of the circuit (independent of the code fetch)
  const payload = {
    num_qubits: state.numQubits,
    shots: state.shots,
    mode: "sim",
    gates: gatePayload(),
  };
  try {
    const res = await fetch("/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      $("export-qiskit").textContent = err.detail || "Export failed.";
      return;
    }
    const data = await res.json();
    $("export-qiskit").textContent = data.qiskit;
    $("export-qasm").textContent = data.qasm;
  } catch (err) {
    $("export-qiskit").textContent = "Couldn't reach the server: " + err;
  }
}

function closeExport() { $("export-modal").classList.add("hidden"); }

// ---- Fullscreen circuit view -----------------------------------------------
// A near-fullscreen overlay showing the circuit picture at a large size. The
// professor's "reading your circuit" thumbnail opens it (click to enlarge); the
// same SVG drives both, so the big view matches the bubble exactly.
function openCircuitModal(src) {
  const modal = $("circuit-modal");
  if (!modal || !src) return;
  $("circuit-modal-img").src = src;
  modal.classList.remove("hidden");
}

function closeCircuitModal() {
  const modal = $("circuit-modal");
  if (modal) modal.classList.add("hidden");
}

// Inline an SVG string as a data URL (no async rasterization needed; it stays
// crisp at any size, which is what the fullscreen view wants).
function svgDataUrl(svg) {
  return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
}

// ---- General error modal ---------------------------------------------------
// A last-resort surface for failures that have no dedicated place to show them
// (unexpected exceptions, unhandled promise rejections, explicit-action errors
// like a failed Run). Errors that already have a home — a rejected /explain in
// the professor pane, a failed hardware job as a queue card, an export error in
// the code box — keep using that home; don't route those here.
function showError(message, title) {
  const modal = $("error-modal");
  if (!modal) return;
  $("error-modal-msg").textContent = String(message || "An unexpected error occurred.");
  $("error-modal-title").textContent = title || "Something went wrong";
  modal.classList.remove("hidden");
  const ok = $("error-modal-ok");
  if (ok) ok.focus();
}

function closeErrorModal() {
  const modal = $("error-modal");
  if (modal) modal.classList.add("hidden");
}

// ---- Circuit → SVG → PNG ---------------------------------------------------
// Render the circuit as a self-contained SVG (inline colors, no external CSS),
// then rasterize it to a PNG entirely in the browser — no backend, no deps. The
// gate shapes mirror placeGate()'s DOM so the picture matches the canvas.
const SVG_COLORS = {
  gate: "#e0506a", rot: "#a96ae0", ctrl: "#4b8bff",
  panel: "#171a21", text: "#d6dae2", muted: "#8b93a3", wire: "#3a4150",
};

function svgEsc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// SVG fragment for one gate, matching the cases in placeGate().
function gateSVG(g, spec, x, ys) {
  const C = SVG_COLORS;
  const box = (cy, fill, label, ang) => {
    let r = `<rect x="${x - 18}" y="${cy - 18}" width="36" height="36" rx="6" fill="${fill}"/>`;
    if (ang != null) {
      r += `<text x="${x}" y="${cy - 2}" fill="#fff" font-size="11" font-weight="700" text-anchor="middle">${svgEsc(label)}</text>`;
      r += `<text x="${x}" y="${cy + 11}" fill="#fff" font-size="8" text-anchor="middle">${svgEsc(ang)}</text>`;
    } else {
      r += `<text x="${x}" y="${cy + 5}" fill="#fff" font-size="13" font-weight="700" text-anchor="middle">${svgEsc(label)}</text>`;
    }
    return r;
  };
  const dot = (cy) => `<circle cx="${x}" cy="${cy}" r="6" fill="${C.ctrl}"/>`;
  const target = (cy) =>
    `<circle cx="${x}" cy="${cy}" r="13" fill="${C.panel}" stroke="${C.ctrl}" stroke-width="2"/>` +
    `<text x="${x}" y="${cy + 6}" fill="${C.ctrl}" font-size="18" font-weight="700" text-anchor="middle">⊕</text>`;
  const swapx = (cy) => `<text x="${x}" y="${cy + 6}" fill="${C.ctrl}" font-size="18" font-weight="800" text-anchor="middle">✕</text>`;
  const cphase = (cy) =>
    `<rect x="${x - 15}" y="${cy - 15}" width="30" height="30" rx="6" fill="${C.ctrl}"/>` +
    `<text x="${x}" y="${cy - 2}" fill="#fff" font-size="12" font-weight="700" text-anchor="middle">P</text>` +
    `<text x="${x}" y="${cy + 10}" fill="#fff" font-size="8" text-anchor="middle">${svgEsc(degStr(g.param))}</text>`;

  if (spec.arity === 1) return box(ys[0], spec.param ? C.rot : C.gate, g.label, spec.param ? degStr(g.param) : null);
  if (g.label === "CX") return dot(ys[0]) + target(ys[1]);
  if (g.label === "CZ") return dot(ys[0]) + dot(ys[1]);
  if (g.label === "SWAP") return swapx(ys[0]) + swapx(ys[1]);
  if (g.label === "CP") return dot(ys[0]) + cphase(ys[1]);
  if (g.label === "CCX") return dot(ys[0]) + dot(ys[1]) + target(ys[2]);
  if (g.label === "CSWAP") return dot(ys[0]) + swapx(ys[1]) + swapx(ys[2]);
  return "";
}

// A standalone SVG string for the whole circuit, plus its pixel dimensions.
function circuitSVG() {
  const C = SVG_COLORS;
  const n = state.numQubits;
  const cols = state.gates.length;
  const W = LABEL_W + (cols + 1) * COL_W;
  const H = n * ROW_H;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" ` +
    `font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif">`;
  s += `<rect width="${W}" height="${H}" fill="${C.panel}"/>`;
  for (let q = 0; q < n; q++) {
    const y = rowCenter(q);
    s += `<line x1="${LABEL_W}" y1="${y}" x2="${W}" y2="${y}" stroke="${C.wire}" stroke-width="2"/>`;
    const one = state.initialStates[q] === 1;
    s += `<text x="8" y="${y - 4}" fill="${C.muted}" font-size="12" font-style="italic">q${q}</text>`;
    s += `<text x="8" y="${y + 12}" fill="${one ? C.ctrl : C.muted}" font-size="11">${one ? "|1⟩" : "|0⟩"}</text>`;
  }
  state.gates.forEach((g, c) => {
    const spec = GATES[g.label];
    const x = colCenter(c);
    const ys = g.qubits.map(rowCenter);
    if (spec.arity >= 2) {
      const y0 = Math.min(...ys), y1 = Math.max(...ys);
      s += `<line x1="${x}" y1="${y0}" x2="${x}" y2="${y1}" stroke="${C.ctrl}" stroke-width="2"/>`;
    }
    s += gateSVG(g, spec, x, ys);
  });
  s += `</svg>`;
  return { svg: s, W, H };
}

// Rasterize an SVG string to a PNG (2× for crispness). Resolves with a Blob and
// a data URL; rejects if the browser can't load the SVG into an <img>.
function svgToPng(svg, W, H, scale = 2) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = W * scale;
      canvas.height = H * scale;
      const ctx = canvas.getContext("2d");
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
      const dataUrl = canvas.toDataURL("image/png");
      canvas.toBlob((b) => (b ? resolve({ blob: b, dataUrl }) : reject(new Error("toBlob failed"))), "image/png");
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("SVG failed to load")); };
    img.src = url;
  });
}

// Prepare the circuit PNG for the export modal's Download button. There's no
// on-screen preview (the modal just offers the button), so this only rasterizes
// the diagram and arms the button — it disables itself if rasterization fails.
let lastCircuitPng = null;
async function renderCircuitDiagram() {
  const dlBtn = $("export-png-download");
  lastCircuitPng = null;
  if (dlBtn) dlBtn.disabled = true;
  const { svg, W, H } = circuitSVG();
  try {
    const { blob } = await svgToPng(svg, W, H, 2);
    lastCircuitPng = blob;
    if (dlBtn) dlBtn.disabled = false;
  } catch (_) { /* PNG export unavailable — leave the button disabled */ }
}

function downloadCircuitPng() {
  if (!lastCircuitPng) return;
  downloadBlob(lastCircuitPng, "circuit.png");
}

// Copy a code block to the clipboard, flashing the button label as feedback.
// Prefer the async Clipboard API, but fall back to a hidden-textarea + execCommand
// so copy still works where that API is unavailable (older browsers, some
// non-secure contexts).
async function copyExport(btn) {
  const text = $(btn.dataset.copy).textContent;
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch (_) { /* fall through to the textarea path */ }
  if (!ok) ok = legacyCopy(text);
  flashButton(btn, ok ? "Copied!" : "Copy failed");
}

function legacyCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
  ta.remove();
  return ok;
}

function flashButton(btn, label) {
  const old = btn.dataset.label || (btn.dataset.label = btn.textContent);
  btn.textContent = label;
  clearTimeout(btn._flash);
  btn._flash = setTimeout(() => { btn.textContent = old; }, 1200);
}
