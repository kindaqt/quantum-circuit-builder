// ---- App state, geometry, and circuit mutations ----------------------------
// The single source of truth for the circuit being built (`state`), plus the
// pure-data helpers that read or mutate it. Edits call update() (defined in
// circuit.js) to re-render and re-simulate; that cross-file call is fine because
// every mutation here only runs on user interaction, by which point all the
// <script> modules have loaded into the shared global scope.

// ---- State -----------------------------------------------------------------
// Upper bound on qubit lines. Mirrors the backend's QCB_MAX_QUBITS (fetched in
// loadConfig); the fallback only applies until /config responds. The cap exists
// because exact statevector simulation costs 2^n — it isn't a UI whim.
let MAX_QUBITS = 16;
let nextId = 1;
const state = { numQubits: 2, shots: 1024, gates: [], runMode: "sim", initialStates: [0, 0] };
// runMode: "sim" = live local statevector; "quantum" = run on IBM hardware on
// demand (only available when the backend reports quantum_enabled).
// gate = { id, label, qubits:[...], param: radians|null }
// initialStates[q] = 0 | 1 — the basis state each qubit starts in. Starting a
// qubit in |1> is identical to an X at the very front of the circuit, so we
// inject those prep gates only at run time (see gatePayload), never as
// draggable gates on the canvas.

// ---- Geometry --------------------------------------------------------------
const LABEL_W = 46, COL_W = 58, ROW_H = 58;
function rowCenter(q) { return q * ROW_H + ROW_H / 2; }
function colCenter(c) { return LABEL_W + c * COL_W + COL_W / 2; }

// ---- DOM refs --------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const scroll = $("circuit-scroll");
const inner = $("circuit-inner");
const wires = $("wires");

// ---- Shared helpers --------------------------------------------------------
// Minimal escaping for user-facing strings injected into HTML attributes/text.
function escapeAttr(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
// Format a radian angle as a whole-degree string (e.g. "90°").
function degStr(rad) { return Math.round((rad * 180) / Math.PI) + "°"; }

// ---- Mutations -------------------------------------------------------------
function addGate(label, qubit) {
  const spec = GATES[label];
  if (!spec) return;
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
  update();
}

function cycleEndpoint(id, idx) {
  const g = state.gates.find((x) => x.id === id);
  if (!g) return;
  const used = new Set(g.qubits.filter((_, i) => i !== idx));
  let q = g.qubits[idx];
  do { q = (q + 1) % state.numQubits; } while (used.has(q));
  g.qubits[idx] = q;
  update();
}

function setQubits(n) {
  n = Math.max(1, Math.min(MAX_QUBITS, n));
  state.numQubits = n;
  state.gates = state.gates.filter((g) => g.qubits.every((q) => q < n));
  while (state.initialStates.length < n) state.initialStates.push(0);
  state.initialStates.length = n;
  $("q-count").textContent = n;
  update();
}

// Flip a qubit's initial state between |0> and |1>.
function toggleInitialState(q) {
  state.initialStates[q] = state.initialStates[q] ? 0 : 1;
  update();
}

// The current circuit in the backend's wire format, prepending an X on every
// qubit whose initial state is |1>. These synthetic prep gates live only in the
// payload — they are never placed on the canvas, so drag/move logic is unaffected.
function gatePayload() {
  const prep = [];
  for (let q = 0; q < state.numQubits; q++) {
    if (state.initialStates[q]) prep.push({ name: GATES.X.m, qubits: [q], param: null });
  }
  const placed = state.gates.map((g) => ({ name: GATES[g.label].m, qubits: g.qubits, param: g.param }));
  return [...prep, ...placed];
}

// Is there anything meaningful to simulate/explain? A flipped initial state
// counts even with no placed gates.
function hasContent() {
  return state.gates.length > 0 || state.initialStates.some(Boolean);
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
  update();
}
