// ---- Gate catalog ----------------------------------------------------------
// arity = number of qubits, param = takes a rotation/phase angle.
const GATES = {
  H:  { m: "h", arity: 1, desc: "Hadamard — creates superposition; maps |0⟩→(|0⟩+|1⟩)/√2." },
  X:  { m: "x", arity: 1, desc: "Pauli-X (NOT) — flips |0⟩↔|1⟩; π rotation about X." },
  Y:  { m: "y", arity: 1, desc: "Pauli-Y — bit+phase flip; π rotation about Y." },
  Z:  { m: "z", arity: 1, desc: "Pauli-Z — phase flip; sends |1⟩→−|1⟩." },
  S:  { m: "s", arity: 1, desc: "S (phase) — quarter turn; applies +i phase to |1⟩ (Z½)." },
  T:  { m: "t", arity: 1, desc: "T (π/8) — eighth turn; applies e^{iπ/4} phase to |1⟩ (Z¼)." },
  SDG: { m: "sdg", arity: 1, desc: "S† — inverse of S; applies −i phase to |1⟩." },
  TDG: { m: "tdg", arity: 1, desc: "T† — inverse of T; applies e^{−iπ/4} phase to |1⟩." },
  RX: { m: "rx", arity: 1, param: true, desc: "RX(θ) — rotation about the X axis by an adjustable angle." },
  RY: { m: "ry", arity: 1, param: true, desc: "RY(θ) — rotation about the Y axis by an adjustable angle." },
  RZ: { m: "rz", arity: 1, param: true, desc: "RZ(θ) — rotation about the Z axis by an adjustable angle." },
  CX: { m: "cx", arity: 2, desc: "CNOT — flips the target qubit when the control is |1⟩." },
  CZ: { m: "cz", arity: 2, desc: "Controlled-Z — applies a phase flip when both qubits are |1⟩." },
  SWAP: { m: "swap", arity: 2, desc: "SWAP — exchanges the states of two qubits." },
  CP: { m: "cp", arity: 2, param: true, desc: "Controlled-phase — adds an adjustable phase when both qubits are |1⟩." },
  CCX: { m: "ccx", arity: 3, desc: "Toffoli (CCX) — flips the target when both controls are |1⟩." },
  CSWAP: { m: "cswap", arity: 3, desc: "Fredkin (CSWAP) — swaps two qubits when the control is |1⟩." },
};

const P = Math.PI;
const a = (label, qubits, param = null) => ({ label, qubits, param });

// ---- Predefined algorithms (verified against Qiskit) ----------------------
// Each: required qubit count, gate sequence, and a one-line explanation.
const ALGOS = {
  "Bell": {
    n: 2,
    desc: "Maximally entangled pair: equal |00⟩ and |11⟩.",
    gates: [a("H", [0]), a("CX", [0, 1])],
  },
  "GHZ": {
    n: 3,
    desc: "3-qubit entanglement: equal |000⟩ and |111⟩.",
    gates: [a("H", [0]), a("CX", [0, 1]), a("CX", [1, 2])],
  },
  "Grover": {
    n: 2,
    desc: "2-qubit search marking |11⟩ — amplifies to 100%.",
    gates: [
      a("H", [0]), a("H", [1]), a("CZ", [0, 1]),
      a("H", [0]), a("H", [1]), a("X", [0]), a("X", [1]),
      a("CZ", [0, 1]), a("X", [0]), a("X", [1]), a("H", [0]), a("H", [1]),
    ],
  },
  "Deutsch–Jozsa": {
    n: 3,
    desc: "Balanced oracle: inputs q0,q1 measure |11⟩ (non-zero ⇒ balanced).",
    gates: [
      a("X", [2]), a("H", [0]), a("H", [1]), a("H", [2]),
      a("CX", [0, 2]), a("CX", [1, 2]),
      a("H", [0]), a("H", [1]),
    ],
  },
  "Bernstein–Vazirani": {
    n: 4,
    desc: "Recovers hidden string s=101 in one query (read q0,q1,q2).",
    gates: [
      a("X", [3]), a("H", [0]), a("H", [1]), a("H", [2]), a("H", [3]),
      a("CX", [0, 3]), a("CX", [2, 3]),
      a("H", [0]), a("H", [1]), a("H", [2]),
    ],
  },
  "QFT": {
    n: 3,
    desc: "3-qubit Quantum Fourier Transform.",
    gates: [
      a("H", [0]), a("CP", [1, 0], P / 2), a("CP", [2, 0], P / 4),
      a("H", [1]), a("CP", [2, 1], P / 2),
      a("H", [2]), a("SWAP", [0, 2]),
    ],
  },
  "Inverse QFT": {
    n: 3,
    desc: "Undoes the 3-qubit QFT.",
    gates: [
      a("SWAP", [0, 2]), a("H", [2]), a("CP", [2, 1], -P / 2),
      a("H", [1]), a("CP", [2, 0], -P / 4), a("CP", [1, 0], -P / 2),
      a("H", [0]),
    ],
  },
  "Phase estimation": {
    n: 4,
    desc: "Estimates phase 1/8 of a T gate — peak at |100⟩ (read q0q1q2 = 0.001).",
    gates: [
      a("X", [3]), a("H", [0]), a("H", [1]), a("H", [2]),
      a("CP", [0, 3], P / 4), a("CP", [1, 3], P / 2), a("CP", [2, 3], P),
      // inverse QFT on q0,q1,q2 (no final swaps -> clean single peak)
      a("H", [2]), a("CP", [2, 1], -P / 2),
      a("H", [1]), a("CP", [2, 0], -P / 4), a("CP", [1, 0], -P / 2),
      a("H", [0]),
    ],
  },
  "Shor (N=15, a=7)": {
    n: 8,
    desc: "Period-finding for 7 mod 15: 4 equal peaks ⇒ period r=4 ⇒ factors 3,5.",
    gates: shorGates(),
  },
};

function shorGates() {
  const g = [a("H", [0]), a("H", [1]), a("H", [2]), a("H", [3]), a("X", [4])];
  const cU = (c) => [
    a("CSWAP", [c, 4, 5]), a("CSWAP", [c, 5, 6]), a("CSWAP", [c, 6, 7]),
    a("CX", [c, 4]), a("CX", [c, 5]), a("CX", [c, 6]), a("CX", [c, 7]),
  ];
  g.push(...cU(0));                 // q0 controls U^1
  g.push(...cU(1), ...cU(1));       // q1 controls U^2  (q2,q3: U^4 = I, omitted)
  // inverse QFT on q0..q3 (no final swaps)
  g.push(
    a("H", [3]),
    a("CP", [3, 2], -P / 2), a("H", [2]),
    a("CP", [3, 1], -P / 4), a("CP", [2, 1], -P / 2), a("H", [1]),
    a("CP", [3, 0], -P / 8), a("CP", [2, 0], -P / 4), a("CP", [1, 0], -P / 2), a("H", [0]),
  );
  return g;
}

// ---- State -----------------------------------------------------------------
const MAX_QUBITS = 10;
let nextId = 1;
const state = { numQubits: 2, shots: 1024, gates: [], die: null, runMode: "sim" };
// runMode: "sim" = live local statevector; "quantum" = run on IBM hardware on
// demand (only available when the backend reports quantum_enabled).
// gate = { id, label, qubits:[...], param: radians|null }
// state.die holds the active die name (e.g. "d6") when one is loaded, else null.

// Latest per-outcome probabilities from the backend, used to sample a roll.
let lastDistribution = [];

// ---- Geometry --------------------------------------------------------------
const LABEL_W = 46, COL_W = 58, ROW_H = 58;

// ---- DOM refs --------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const scroll = $("circuit-scroll");
const inner = $("circuit-inner");
const wires = $("wires");

// ---- Mutations -------------------------------------------------------------
function addGate(label, qubit) {
  const spec = GATES[label];
  if (!spec) return;
  state.die = null;
  let qubits, param = null;
  if (spec.arity === 1) {
    qubits = [qubit];
    if (spec.param) param = P / 2;
  } else {
    qubits = pickQubits(qubit, spec.arity);
    if (!qubits) return;
    if (spec.param) param = P / 2;
  }
  state.gates.push({ id: nextId++, label, qubits, param });
  update();
}

// Choose `arity` distinct qubits starting at `base`, staying in range.
function pickQubits(base, arity) {
  const n = state.numQubits;
  if (arity > n) return null;
  const out = [base];
  let q = base;
  while (out.length < arity) {
    q = (q + 1) % n;
    if (!out.includes(q)) out.push(q);
  }
  return out;
}

function removeGate(id) {
  state.gates = state.gates.filter((g) => g.id !== id);
  state.die = null;
  update();
}

function cycleEndpoint(id, idx) {
  const g = state.gates.find((x) => x.id === id);
  if (!g) return;
  const used = new Set(g.qubits.filter((_, i) => i !== idx));
  let q = g.qubits[idx];
  do { q = (q + 1) % state.numQubits; } while (used.has(q));
  g.qubits[idx] = q;
  state.die = null;
  update();
}

function setQubits(n) {
  n = Math.max(1, Math.min(MAX_QUBITS, n));
  state.die = null;
  state.numQubits = n;
  state.gates = state.gates.filter((g) => g.qubits.every((q) => q < n));
  $("q-count").textContent = n;
  update();
}

function lookupPreset(name) {
  if (ALGOS[name]) return ALGOS[name];
  if (typeof DICE_CIRCUITS !== "undefined" && DICE_CIRCUITS[name]) return DICE_CIRCUITS[name];
  return null;
}

function applyAlgorithm(name) {
  const algo = lookupPreset(name);
  if (!algo) return;
  setQubits(Math.max(state.numQubits, algo.n));
  algo.gates.forEach((g) => {
    state.gates.push({
      id: nextId++, label: g.label, qubits: [...g.qubits], param: g.param,
    });
  });
  // Mark the circuit as a die roll only when a die preset was loaded.
  state.die = (typeof DICE_CIRCUITS !== "undefined" && DICE_CIRCUITS[name]) ? name : null;
  update();
}

// ---- Rendering -------------------------------------------------------------
function rowCenter(q) { return q * ROW_H + ROW_H / 2; }
function colCenter(c) { return LABEL_W + c * COL_W + COL_W / 2; }

function render() {
  const n = state.numQubits;
  const cols = state.gates.length;
  const contentW = LABEL_W + (cols + 1) * COL_W;
  const fullW = Math.max(contentW, scroll.clientWidth);
  const wiresH = n * ROW_H;
  const fullH = wiresH + 34;

  inner.style.width = fullW + "px";
  inner.style.height = fullH + "px";

  [...inner.querySelectorAll(".gatebox,.node,.cphase,.qlabel,.hint,.add-line,.droprow,.dropcol")]
    .forEach((e) => e.remove());

  let svg = "";
  for (let q = 0; q < n; q++) {
    const y = rowCenter(q);
    svg += `<line x1="${LABEL_W}" y1="${y}" x2="${fullW}" y2="${y}" stroke="#3a4150" stroke-width="2"/>`;
    const lbl = document.createElement("div");
    lbl.className = "qlabel";
    lbl.textContent = `q${q}`;
    lbl.style.left = "12px";
    lbl.style.top = y + "px";
    inner.appendChild(lbl);
  }

  state.gates.forEach((g, c) => {
    const spec = GATES[g.label];
    const x = colCenter(c);
    const ys = g.qubits.map(rowCenter);
    if (spec.arity >= 2) {
      const y0 = Math.min(...ys), y1 = Math.max(...ys);
      svg += `<line x1="${x}" y1="${y0}" x2="${x}" y2="${y1}" stroke="var(--ctrl)" stroke-width="2"/>`;
    }
    placeGate(g, spec, x, ys).forEach((el) => inner.appendChild(el));
  });

  wires.setAttribute("width", fullW);
  wires.setAttribute("height", wiresH);
  wires.innerHTML = svg;

  const addBtn = document.createElement("button");
  addBtn.className = "add-line";
  addBtn.textContent = "+ qubit";
  addBtn.style.top = wiresH + 5 + "px";
  addBtn.disabled = n >= MAX_QUBITS;
  addBtn.title = n >= MAX_QUBITS ? `Max ${MAX_QUBITS} qubits` : "Add a qubit line";
  addBtn.addEventListener("click", () => setQubits(n + 1));
  inner.appendChild(addBtn);

  if (cols === 0) {
    const h = document.createElement("div");
    h.className = "hint";
    h.textContent = "Drag a gate or algorithm onto a qubit line — or click a gate to drop it on q0. Drag a placed gate to move it.";
    h.style.left = "0"; h.style.top = "0"; h.style.position = "absolute";
    h.style.width = "100%";
    inner.appendChild(h);
  }
}

// Redraw the circuit AND re-run the simulation. Use this for circuit edits;
// use bare render() for pure layout changes (e.g. window resize) so that the
// random measurement sampling does not change when only the screen size does.
function update() {
  const rr = $("roll-result");
  if (rr) rr.textContent = "";   // a circuit edit invalidates the last roll
  render();
  scheduleSim();
}

// Build the DOM nodes for a single gate occupying column x.
function placeGate(g, spec, x, ys) {
  const els = [];
  if (spec.arity === 1) {
    els.push(gatebox(g, x, ys[0], spec));
  } else if (g.label === "CX") {
    els.push(node("dot", x, ys[0], g, 0));
    els.push(node("target", x, ys[1], g, 1, "⊕"));
  } else if (g.label === "CZ") {
    els.push(node("dot", x, ys[0], g, 0));
    els.push(node("dot", x, ys[1], g, 1));
  } else if (g.label === "SWAP") {
    els.push(node("swapx", x, ys[0], g, 0, "✕"));
    els.push(node("swapx", x, ys[1], g, 1, "✕"));
  } else if (g.label === "CP") {
    els.push(node("dot", x, ys[0], g, 0));
    els.push(cphaseBox(g, x, ys[1]));
  } else if (g.label === "CCX") {
    els.push(node("dot", x, ys[0], g, 0));
    els.push(node("dot", x, ys[1], g, 1));
    els.push(node("target", x, ys[2], g, 2, "⊕"));
  } else if (g.label === "CSWAP") {
    els.push(node("dot", x, ys[0], g, 0));
    els.push(node("swapx", x, ys[1], g, 1, "✕"));
    els.push(node("swapx", x, ys[2], g, 2, "✕"));
  }
  els.forEach((el) => (el.dataset.gid = g.id)); // tag for drag-dimming
  return els;
}

function withDelete(el, id) {
  const d = document.createElement("div");
  d.className = "del";
  d.textContent = "×";
  d.addEventListener("pointerdown", (e) => e.stopPropagation());
  d.addEventListener("click", (e) => { e.stopPropagation(); removeGate(id); });
  el.appendChild(d);
  return el;
}

function gatebox(g, x, y, spec) {
  const el = document.createElement("div");
  el.className = "gatebox " + (spec.param ? "grot" : "g1");
  el.style.left = x + "px";
  el.style.top = y + "px";
  if (spec.param) {
    el.innerHTML = `<span>${g.label}</span><span class="ang">${degStr(g.param)}</span>`;
    el.title = "Drag to move · click to set angle";
    el.addEventListener("pointerdown", (e) => startGateDrag(g, () => openAngle(g, el), e));
  } else {
    el.textContent = g.label;
    el.title = "Drag to move";
    el.addEventListener("pointerdown", (e) => startGateDrag(g, null, e));
  }
  return withDelete(el, g.id);
}

function cphaseBox(g, x, y) {
  const el = document.createElement("div");
  el.className = "cphase";
  el.style.left = x + "px";
  el.style.top = y + "px";
  el.innerHTML = `<span>P</span><span class="ang">${degStr(g.param)}</span>`;
  el.title = "Drag to move · click to set angle";
  el.addEventListener("pointerdown", (e) => startGateDrag(g, () => openAngle(g, el), e));
  return withDelete(el, g.id);
}

function node(kind, x, y, g, idx, glyph) {
  const el = document.createElement("div");
  el.className = "node " + kind;
  el.style.left = x + "px";
  el.style.top = y + "px";
  if (glyph) el.textContent = glyph;
  el.title = "Drag to move · click to retarget this qubit";
  el.addEventListener("pointerdown", (e) =>
    startGateDrag(g, () => cycleEndpoint(g.id, idx), e));
  return withDelete(el, g.id);
}

function degStr(rad) { return Math.round((rad * 180) / Math.PI) + "°"; }

// ---- Angle popover ---------------------------------------------------------
let angleTarget = null;
function openAngle(g, el) {
  angleTarget = g;
  const pop = $("angle-popover");
  const r = el.getBoundingClientRect();
  pop.style.left = Math.min(r.left, window.innerWidth - 200) + "px";
  pop.style.top = r.bottom + 6 + "px";
  const deg = Math.round((g.param * 180) / Math.PI);
  $("angle-slider").value = deg;
  $("angle-val").textContent = deg;
  pop.classList.remove("hidden");
}
$("angle-slider").addEventListener("input", (e) => {
  const deg = +e.target.value;
  $("angle-val").textContent = deg;
  if (angleTarget) { angleTarget.param = (deg * Math.PI) / 180; update(); }
});
$("angle-done").addEventListener("click", () => {
  $("angle-popover").classList.add("hidden");
  angleTarget = null;
});

// ---- Palettes --------------------------------------------------------------
function chipClass(label) {
  const spec = GATES[label];
  if (spec.arity >= 2) return "c2";
  return spec.param ? "crot" : "c1";
}

function buildPalette() {
  const pal = $("palette");
  for (const label of Object.keys(GATES)) {
    const chip = document.createElement("div");
    chip.className = "chip " + chipClass(label);
    chip.textContent = label;
    chip.title = GATES[label].desc;
    chip.addEventListener("pointerdown", (e) => startNewDrag(label, e));
    pal.appendChild(chip);
  }
  buildPresetPalette($("algos"), ALGOS);
  // Dice are draggable preset circuits, just like the algorithms: dropping one
  // loads its uniform-superposition prep gates onto the canvas, then the top
  // Roll button samples a face from the simulated distribution.
  if (typeof DICE_CIRCUITS !== "undefined") buildPresetPalette($("dice"), DICE_CIRCUITS);
}

function buildPresetPalette(container, presets) {
  for (const name of Object.keys(presets)) {
    const chip = document.createElement("div");
    chip.className = "chip calgo";
    chip.textContent = name;
    chip.title = presets[name].desc;
    chip.addEventListener("pointerdown", (e) => startAlgoDrag(name, e));
    container.appendChild(chip);
  }
}

// ---- Drag from palette (new gate) -----------------------------------------
let drag = null;
function startNewDrag(label, e) { beginDrag({ kind: "gate", label }, label, chipClass(label), e); }
function startAlgoDrag(name, e) { beginDrag({ kind: "algo", name }, name, "calgo", e); }

function beginDrag(payload, text, cls, e) {
  e.preventDefault();
  const ghost = document.createElement("div");
  ghost.className = "chip drag-ghost " + cls;
  ghost.textContent = text;
  document.body.appendChild(ghost);
  document.body.classList.add("dragging");
  drag = { ...payload, ghost, moved: false, x0: e.clientX, y0: e.clientY };
  moveGhost(e.clientX, e.clientY);
  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", endDrag);
}

function moveGhost(x, y) {
  drag.ghost.style.left = x + "px";
  drag.ghost.style.top = y + "px";
}

function overCircuit(clientX, clientY) {
  const r = scroll.getBoundingClientRect();
  return clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom;
}

function qubitAt(clientX, clientY) {
  if (!overCircuit(clientX, clientY)) return null;
  const y = clientY - inner.getBoundingClientRect().top;
  const q = Math.floor(y / ROW_H);
  return q >= 0 && q < state.numQubits ? q : null;
}

function onDragMove(e) {
  if (!drag) return;
  if (Math.abs(e.clientX - drag.x0) + Math.abs(e.clientY - drag.y0) > 4) drag.moved = true;
  moveGhost(e.clientX, e.clientY);
  highlightRow(drag.kind === "gate" ? qubitAt(e.clientX, e.clientY) : null);
}

function endDrag(e) {
  if (!drag) return;
  const { kind, label, name, moved } = drag;
  drag.ghost.remove();
  document.body.classList.remove("dragging");
  highlightRow(null);
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", endDrag);
  drag = null;
  if (kind === "algo") {
    if (overCircuit(e.clientX, e.clientY) || !moved) applyAlgorithm(name);
    return;
  }
  const q = qubitAt(e.clientX, e.clientY);
  if (q !== null) addGate(label, q);
  else if (!moved) addGate(label, 0);
}

// ---- Drag an already-placed gate (move / reorder) -------------------------
let gdrag = null;
function startGateDrag(g, clickAction, e) {
  e.preventDefault();
  e.stopPropagation();
  gdrag = { id: g.id, clickAction, x0: e.clientX, y0: e.clientY, moved: false };
  window.addEventListener("pointermove", onGateMove);
  window.addEventListener("pointerup", endGateMove);
}

// Text shown on the floating ghost while a placed gate is being dragged.
function gateGhostText(g) {
  const spec = GATES[g.label];
  if (g.label === "CP") return "P " + degStr(g.param);
  if (spec.param) return g.label + " " + degStr(g.param);
  return g.label;
}

// Spawn the cursor-following ghost and fade the gate in its old position so the
// user can see the gate itself moving with the mouse.
function startGateGhost(x, y) {
  const g = state.gates.find((gg) => gg.id === gdrag.id);
  if (!g) return;
  const ghost = document.createElement("div");
  ghost.className = "chip drag-ghost " + chipClass(g.label);
  ghost.textContent = gateGhostText(g);
  document.body.appendChild(ghost);
  gdrag.ghost = ghost;
  positionGhost(ghost, x, y);
  inner.querySelectorAll(`[data-gid="${g.id}"]`)
    .forEach((el) => el.classList.add("gate-moving"));
}

function positionGhost(ghost, x, y) {
  ghost.style.left = x + "px";
  ghost.style.top = y + "px";
}

function onGateMove(e) {
  if (!gdrag) return;
  if (!gdrag.moved &&
      Math.abs(e.clientX - gdrag.x0) + Math.abs(e.clientY - gdrag.y0) > 4) {
    gdrag.moved = true;
    document.body.classList.add("dragging");
    startGateGhost(e.clientX, e.clientY);
  }
  if (gdrag.moved) {
    if (gdrag.ghost) positionGhost(gdrag.ghost, e.clientX, e.clientY);
    showMoveHints(e);
  }
}

function endGateMove(e) {
  if (!gdrag) return;
  window.removeEventListener("pointermove", onGateMove);
  window.removeEventListener("pointerup", endGateMove);
  document.body.classList.remove("dragging");
  if (gdrag.ghost) gdrag.ghost.remove();
  clearMoveHints();
  const { id, clickAction, moved } = gdrag;
  gdrag = null;
  if (moved) moveGate(id, e);   // re-render drops the .gate-moving dimming
  else if (clickAction) clickAction();
}

function colFromX(clientX) {
  const left = inner.getBoundingClientRect().left;
  const slot = Math.round((clientX - left - LABEL_W) / COL_W);
  return Math.max(0, Math.min(state.gates.length, slot));
}

function moveGate(id, e) {
  const idx = state.gates.findIndex((g) => g.id === id);
  if (idx < 0) return;
  const g = state.gates[idx];
  const spec = GATES[g.label];
  const q = qubitAt(e.clientX, e.clientY);

  // retarget qubit(s) by vertical drop position
  if (q !== null) {
    if (spec.arity === 1) {
      g.qubits = [q];
    } else {
      const delta = q - g.qubits[0];
      const moved = g.qubits.map((x) => x + delta);
      if (moved.every((x) => x >= 0 && x < state.numQubits)) g.qubits = moved;
    }
  }

  // reorder by horizontal drop position
  let col = colFromX(e.clientX);
  state.gates.splice(idx, 1);
  if (col > idx) col -= 1;
  col = Math.max(0, Math.min(state.gates.length, col));
  state.gates.splice(col, 0, g);
  state.die = null;
  update();
}

function showMoveHints(e) {
  highlightRow(qubitAt(e.clientX, e.clientY));
  let mark = inner.querySelector(".dropcol");
  if (!overCircuit(e.clientX, e.clientY)) { if (mark) mark.remove(); return; }
  if (!mark) {
    mark = document.createElement("div");
    mark.className = "dropcol";
    inner.appendChild(mark);
  }
  const col = colFromX(e.clientX);
  mark.style.left = LABEL_W + col * COL_W + "px";
  mark.style.height = state.numQubits * ROW_H + "px";
}

function clearMoveHints() {
  highlightRow(null);
  const mark = inner.querySelector(".dropcol");
  if (mark) mark.remove();
}

function highlightRow(q) {
  let band = inner.querySelector(".droprow");
  if (q === null) { if (band) band.remove(); return; }
  if (!band) {
    band = document.createElement("div");
    band.className = "droprow";
    inner.appendChild(band);
  }
  band.style.top = q * ROW_H + "px";
  band.style.height = ROW_H + "px";
}

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

async function runSim(mode = state.runMode) {
  const quantum = mode === "quantum";
  if (quantum) {
    setRunStatus("Submitting…", "busy");
    $("run-hw").disabled = true;
    $("histogram").innerHTML =
      `<div class="hint">Submitting circuit to quantum hardware — this can sit in the queue for a while.</div>`;
  }
  const payload = {
    num_qubits: state.numQubits,
    shots: state.shots,
    mode,
    gates: state.gates.map((g) => ({ name: GATES[g.label].m, qubits: g.qubits, param: g.param })),
  };
  try {
    const res = await fetch("/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      $("histogram").innerHTML = `<div class="hint">${err.detail || "Run rejected."}</div>`;
      if (quantum) setRunStatus("Failed", "err");
      return;
    }
    const data = await res.json();
    renderResults(data);
    if (quantum) setRunStatus(`Ran on ${data.backend}`, "");
  } catch (err) {
    $("histogram").innerHTML = `<div class="hint">Run error: ${err}</div>`;
    if (quantum) setRunStatus("Error", "err");
  } finally {
    if (quantum) $("run-hw").disabled = false;
  }
}

function renderResults(data) {
  lastDistribution = data.statevector || [];
  renderHistogram(data.counts);
  if (data.mode === "quantum") {
    // Real hardware returns measurement counts only — amplitudes and Bloch
    // vectors aren't physically observable.
    const note = (what) =>
      `<div class="hint">${what} isn't measurable on real quantum hardware — only measurement counts are returned.</div>`;
    $("statevector").innerHTML = note("The statevector");
    $("bloch").innerHTML = note("Bloch vectors");
  } else {
    renderStatevector(data.statevector);
    renderBloch(data.bloch);
  }
}

// ---- Roll: collapse the circuit to one random measured outcome -------------
function sampleOutcome() {
  if (!lastDistribution.length) return null;
  const total = lastDistribution.reduce((s, e) => s + e.prob, 0);
  let r = Math.random() * total;
  for (const e of lastDistribution) { r -= e.prob; if (r <= 0) return e.basis; }
  return lastDistribution[lastDistribution.length - 1].basis;
}

// A die dN maps measured value 0..N-1 to a face 1..N; any other circuit shows
// the measured basis state as a plain integer.
function rollLabel(value) {
  if (state.die) {
    const N = parseInt(state.die.slice(1), 10);
    return `${state.die}: ${(value % N) + 1}`;
  }
  return `${value}`;
}

let rolling = false;
function roll() {
  if (rolling) return;
  const finalBasis = sampleOutcome();
  const el = $("roll-result");
  if (finalBasis === null) { el.textContent = "—"; return; }
  const faces = state.die ? parseInt(state.die.slice(1), 10) : 2 ** state.numQubits;
  rolling = true;
  el.classList.add("rolling");
  let ticks = 0;
  const iv = setInterval(() => {
    el.textContent = rollLabel(Math.floor(Math.random() * faces)); // cosmetic flicker
    if (++ticks >= 8) {
      clearInterval(iv);
      el.classList.remove("rolling");
      el.textContent = rollLabel(parseInt(finalBasis, 2));          // real sample
      rolling = false;
    }
  }, 55);
}

function renderHistogram(counts) {
  const el = $("histogram");
  el.innerHTML = "";
  const keys = Object.keys(counts).sort();
  const max = Math.max(1, ...Object.values(counts));
  for (const k of keys) {
    const wrap = document.createElement("div");
    wrap.className = "hbar";
    wrap.innerHTML =
      `<div class="cnt">${counts[k]}</div>` +
      `<div class="bar" style="height:${(counts[k] / max) * 100}%"></div>` +
      `<div class="lbl">|${k}⟩</div>`;
    el.appendChild(wrap);
  }
}

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
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("panel-" + tab.dataset.tab).classList.add("active");
  });
});

// ---- Run mode (classical simulator vs IBM quantum hardware) ----------------
function setRunStatus(text, kind) {
  const el = $("run-status");
  if (!el) return;
  el.textContent = text;
  el.className = "run-status" + (kind ? " " + kind : "");
}

function setRunMode(mode) {
  state.runMode = mode;
  $("run-hw").hidden = mode !== "quantum";
  $("roll").disabled = mode === "quantum"; // rolls sample the local distribution
  if (mode === "quantum") {
    clearTimeout(simTimer); // cancel any pending live-sim so it can't overwrite results
    setRunStatus("Press Run to submit", "");
  } else {
    setRunStatus("", "");
    scheduleSim(); // resume live local simulation
  }
}

async function loadConfig() {
  try {
    const res = await fetch("/config");
    if (!res.ok) return;
    const cfg = await res.json();
    if (cfg.quantum_enabled) {
      $("run-mode-ctrl").hidden = false;
      const opt = document.querySelector('#run-mode option[value="quantum"]');
      if (opt && cfg.backend) opt.textContent = `Quantum hardware (${cfg.backend})`;
    }
  } catch (_) { /* config is best-effort; app still works as a classical sim */ }
}

// ---- Toolbar wiring --------------------------------------------------------
$("q-minus").addEventListener("click", () => setQubits(state.numQubits - 1));
$("q-plus").addEventListener("click", () => setQubits(state.numQubits + 1));
$("shots").addEventListener("change", (e) => { state.shots = +e.target.value; scheduleSim(); });
$("undo").addEventListener("click", () => { state.gates.pop(); state.die = null; update(); });
$("clear").addEventListener("click", () => { state.gates = []; state.die = null; update(); });
$("roll").addEventListener("click", roll);
$("run-mode").addEventListener("change", (e) => setRunMode(e.target.value));
$("run-hw").addEventListener("click", () => runSim("quantum"));

// Pure layout on resize — must NOT re-sample the simulation.
window.addEventListener("resize", render);

// ---- Init ------------------------------------------------------------------
buildPalette();
update();
loadConfig();
