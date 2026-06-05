// ---- Circuit canvas: rendering, gate placement, drag & drop ----------------
// Everything that draws the circuit and lets you build it by pointer: the SVG
// wires + gate DOM, the palettes you drag from, moving/retargeting placed gates,
// the per-gate angle popover, and the styled hover tooltips. update() (re-render +
// re-simulate) lives here too — it's the entry point every mutation calls.

// ---- Rendering -------------------------------------------------------------
function render() {
  const n = state.numQubits;
  const cols = state.gates.length;
  const contentW = LABEL_W + (cols + 1) * COL_W;
  const fullW = Math.max(contentW, scroll.clientWidth);
  const wiresH = n * ROW_H;
  const fullH = wiresH + 34;

  inner.style.width = fullW + "px";
  inner.style.height = fullH + "px";

  [...inner.querySelectorAll(".gatebox,.node,.cphase,.qlabel,.qgutter,.hint,.add-line,.droprow,.dropcol")]
    .forEach((e) => e.remove());

  // Opaque strip behind the qubit labels so placed gates slide under them
  // (rather than over them) when the circuit is scrolled horizontally. It is
  // pinned to the viewport's left edge by the same --sx transform as the labels.
  const gutter = document.createElement("div");
  gutter.className = "qgutter";
  gutter.style.height = fullH + "px";
  inner.appendChild(gutter);

  let svg = "";
  for (let q = 0; q < n; q++) {
    const y = rowCenter(q);
    svg += `<line x1="${LABEL_W}" y1="${y}" x2="${fullW}" y2="${y}" stroke="#3a4150" stroke-width="2"/>`;
    const lbl = document.createElement("div");
    lbl.className = "qlabel";
    lbl.style.left = "8px";
    lbl.style.top = y + "px";
    const id = document.createElement("span");
    id.className = "qid";
    id.textContent = `q${q}`;
    const one = state.initialStates[q] === 1;
    const ket = document.createElement("button");
    ket.className = "qinit" + (one ? " one" : "");
    ket.textContent = one ? "|1⟩" : "|0⟩";
    ket.title = `Initial state of q${q} — click to flip between |0⟩ and |1⟩`;
    ket.addEventListener("click", () => toggleInitialState(q));
    lbl.appendChild(id);
    lbl.appendChild(ket);
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

  renderStats();

  const addBtn = document.createElement("button");
  addBtn.className = "add-line";
  addBtn.textContent = "+ qubit";
  addBtn.style.top = wiresH + 5 + "px";
  addBtn.disabled = n >= MAX_QUBITS;
  addBtn.title = n >= MAX_QUBITS ? `Max ${MAX_QUBITS} qubits` : "Add a qubit line";
  addBtn.addEventListener("click", () => setQubits(n + 1));
  inner.appendChild(addBtn);

  // Companion "− qubit" button: drops the last wire (and any gates on it).
  // Disabled at the 1-qubit floor. Sits just to the right of "+ qubit".
  const removeBtn = document.createElement("button");
  removeBtn.className = "add-line remove-line";
  removeBtn.textContent = "− qubit";
  removeBtn.style.top = wiresH + 5 + "px";
  removeBtn.disabled = n <= 1;
  removeBtn.title = n <= 1 ? "At least 1 qubit" : "Remove the last qubit line";
  removeBtn.addEventListener("click", () => setQubits(n - 1));
  inner.appendChild(removeBtn);

  if (cols === 0) {
    const h = document.createElement("div");
    h.className = "hint";
    h.textContent = "Drag a gate or algorithm onto a qubit line — or click a gate to drop it on q0. Drag a placed gate to move it.";
    h.style.left = "0"; h.style.top = "0"; h.style.position = "absolute";
    h.style.width = "100%";
    inner.appendChild(h);
  }
}

// ---- Circuit stats (qubits · depth · gates) --------------------------------
// Depth is the standard ASAP layering: each gate sits one level past the busiest
// wire it touches, and a multi-qubit gate occupies a single level across all its
// qubits — matching Qiskit's circuit.depth(). Computed over the placed gates so
// it tracks exactly what's on the canvas; cheap, so it runs on every render.
function circuitStats() {
  const n = state.numQubits;
  const level = new Array(n).fill(0);
  for (const g of state.gates) {
    const base = Math.max(0, ...g.qubits.map((q) => level[q]));
    g.qubits.forEach((q) => { level[q] = base + 1; });
  }
  const depth = state.gates.length ? Math.max(0, ...level) : 0;
  return { n, depth, gates: state.gates.length };
}

function renderStats() {
  const el = $("circuit-stats");
  if (!el) return;
  const { n, depth, gates } = circuitStats();
  const plural = (k, w) => `${k} ${w}${k === 1 ? "" : "s"}`;
  el.textContent = `${plural(n, "qubit")} · depth ${depth} · ${plural(gates, "gate")}`;
}

// Redraw the circuit AND re-run the simulation. Use this for circuit edits;
// use bare render() for pure layout changes (e.g. window resize) so that the
// random measurement sampling does not change when only the screen size does.
function update() {
  resetExplainOnEdit();   // a circuit edit invalidates any prior AI explanation
  render();
  scheduleSim();
}

// An edit means a different circuit, so a fresh run should re-explain rather than
// dedupe against the old one — clear the fingerprint. We deliberately keep the
// conversation log: the professor's history (and the student's questions) carry
// across edits, and the latest message always reflects the current circuit.
function resetExplainOnEdit() {
  lastExplainedSig = null;
  const btn = $("explain-btn");
  // The overview button stays clickable even on an empty canvas: rather than a
  // dead greyed-out button (which looks broken), a click with nothing placed
  // shows a friendly "add a gate or ask a question" hint (see explainCircuit).
  // We only force-disable it while a request is in flight.
  if (btn && !explaining) btn.disabled = false;
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

// ---- Hover tooltips (palette chips only) -----------------------------------
// Native `title` tooltips are plain and their timing varies by browser. These
// styled tooltips appear after a ~1s hover and explain what a gate/algorithm/die
// does, matching the app's theme. Applied to palette chips only — placed gates
// intentionally stay tooltip-free so they don't clutter the canvas.
let tipEl = null, tipTimer = null;
// `text` may be a string or a getter function (for content that changes over time,
// e.g. the professor avatar whose blurb depends on the active persona).
function attachTip(el, text) {
  if (!text || !el) return;
  const get = typeof text === "function" ? text : () => text;
  const t0 = get();
  if (t0) el.setAttribute("aria-label", t0); // stays accessible without a native title
  el.addEventListener("pointerenter", () => {
    clearTimeout(tipTimer);
    const t = get();
    if (t) el.setAttribute("aria-label", t);
    tipTimer = setTimeout(() => { if (t) showTip(el, t); }, 1000); // ~a second of hover
  });
  const hide = () => { clearTimeout(tipTimer); if (tipEl) tipEl.classList.add("hidden"); };
  el.addEventListener("pointerleave", hide);
  el.addEventListener("pointerdown", hide); // a drag/click is starting — get out of the way
}
function showTip(el, text) {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "tip hidden";
    document.body.appendChild(tipEl);
  }
  tipEl.textContent = text;
  tipEl.classList.remove("hidden");
  const r = el.getBoundingClientRect();
  const tr = tipEl.getBoundingClientRect();
  // Centre under the chip (the palettes sit at the top of the page), clamped to
  // stay on-screen.
  const left = Math.max(8, Math.min(r.left + r.width / 2 - tr.width / 2, window.innerWidth - tr.width - 8));
  tipEl.style.left = left + "px";
  tipEl.style.top = (r.bottom + 8) + "px";
}

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
    attachTip(chip, GATES[label].desc);
    chip.addEventListener("pointerdown", (e) => startNewDrag(label, e));
    pal.appendChild(chip);
  }
  buildPresetPalette($("algos"), ALGOS, "algo");
  // Dice are dual-purpose preset circuits: *drag* one onto the canvas to load its
  // uniform-superposition prep gates (a flat distribution over the die's faces),
  // or *click* it like a button to roll it and flash the result (see rollDie).
  if (typeof DICE_CIRCUITS !== "undefined") buildPresetPalette($("dice"), DICE_CIRCUITS, "dice");
}

// Renders a row of preset chips. Shared by the algorithms and the dice (each
// `presets[name]` just needs a `desc` and a gate list). `kind` decides what a
// chip does: an "algo" chip always loads its gates; a "dice" chip loads on drag
// but rolls on a plain click (disambiguated by the drag-distance threshold).
function buildPresetPalette(container, presets, kind) {
  for (const name of Object.keys(presets)) {
    const chip = document.createElement("div");
    chip.className = "chip calgo";
    chip.textContent = name;
    attachTip(chip, presets[name].desc);
    chip.addEventListener("pointerdown",
      (e) => (kind === "dice" ? startDiceDrag(name, e) : startAlgoDrag(name, e)));
    container.appendChild(chip);
  }
}

// ---- Drag from palette (new gate) -----------------------------------------
let drag = null;
function startNewDrag(label, e) { beginDrag({ kind: "gate", label }, label, chipClass(label), e); }
function startAlgoDrag(name, e) { beginDrag({ kind: "algo", name }, name, "calgo", e); }
function startDiceDrag(name, e) { beginDrag({ kind: "dice", name }, name, "calgo", e); }

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
  if (kind === "dice") {
    // A drag onto the canvas loads the prep gates; a plain click rolls the die.
    if (moved) { if (overCircuit(e.clientX, e.clientY)) applyAlgorithm(name); }
    else rollDie(name);
    return;
  }
  const q = qubitAt(e.clientX, e.clientY);
  if (q !== null) addGate(label, q);
  else if (!moved) addGate(label, 0);
}

// ---- Drag an already-placed gate (move / reorder) -------------------------
// A pointerdown on a placed gate is ambiguous: it might be a *click* (open the
// angle popover / retarget a qubit) or the start of a *drag* (move the gate).
// We disambiguate by distance — see onGateMove's threshold. `clickAction` is the
// behavior to run if the pointer never moved far enough to count as a drag.
let gdrag = null;
function startGateDrag(g, clickAction, e) {
  e.preventDefault();
  e.stopPropagation(); // don't let the body-level palette drag also fire
  // x0/y0 = pointer origin, used to measure drag distance; moved flips once we
  // cross the threshold. Listeners are on window so the drag survives the pointer
  // leaving the small gate element.
  gdrag = { id: g.id, clickAction, x0: e.clientX, y0: e.clientY, moved: false };
  window.addEventListener("pointermove", onGateMove);
  window.addEventListener("pointerup", endGateMove);
}

// Label for the floating ghost while a placed gate is dragged. Parameterized
// gates show their angle too; CP renders as "P" on the canvas, so match that.
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
  // Fade every DOM node belonging to this gate (a multi-qubit gate has several:
  // control dots, target, etc.) so its old spot reads as "being moved". The
  // data-gid tags are set in placeGate; the dimming clears on the next render.
  inner.querySelectorAll(`[data-gid="${g.id}"]`)
    .forEach((el) => el.classList.add("gate-moving"));
}

function positionGhost(ghost, x, y) {
  ghost.style.left = x + "px";
  ghost.style.top = y + "px";
}

function onGateMove(e) {
  if (!gdrag) return;
  // Once the pointer has travelled >4px (Manhattan distance) we commit to a drag:
  // create the ghost lazily here (not on pointerdown) so a plain click never
  // spawns one. The threshold also absorbs tiny jitter on a real click.
  if (!gdrag.moved &&
      Math.abs(e.clientX - gdrag.x0) + Math.abs(e.clientY - gdrag.y0) > 4) {
    gdrag.moved = true;
    document.body.classList.add("dragging");
    startGateGhost(e.clientX, e.clientY);
  }
  if (gdrag.moved) {
    if (gdrag.ghost) positionGhost(gdrag.ghost, e.clientX, e.clientY); // follow cursor
    showMoveHints(e); // highlight the target row + show the drop-column marker
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
