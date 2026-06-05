// ---- Dice rolling ----------------------------------------------------------
// Clicking a dice chip rolls it: a genuine quantum measurement of the die's prep
// circuit, shown in a transient modal (with a nat-20 fireworks easter egg). This
// is independent of the canvas — rolling never edits the circuit on screen. The
// dice prep circuits themselves live in the generated dice.js (DICE_CIRCUITS).

// ---- Roll a die (click a dice chip) ----------------------------------------
// Rolling is a genuine quantum measurement: we run the die's prep circuit once
// (1 shot) and read the single outcome bitstring. Qiskit is little-endian, so
// parseInt(bits, 2) already gives the integer face 0..N-1; we show 1..N to match
// how a physical die reads. This is independent of the canvas — rolling never
// edits the circuit on screen.
//
// The roll honours the current Run mode: in "quantum" mode it submits to the
// configured quantum hardware (which can queue), so the modal shows a slot-
// machine of random faces cycling until the real result lands; in classical
// mode it runs instantly on the local Aer simulator ("qsim") and reveals at once.
let rollTimer = null;
let rollCycleTimer = null;
async function rollDie(name) {
  const die = (typeof DICE_CIRCUITS !== "undefined") ? DICE_CIRCUITS[name] : null;
  if (!die) return;
  const quantum = state.runMode === "quantum";
  const faces = parseInt(name.slice(1), 10) || (1 << die.n); // d20 -> 20, etc.
  const dice = rollCount();
  openRollModal(name, dice);
  if (quantum) startRollCycle(faces); // animate while the job is in flight
  // Rolling N dice is N independent measurements of the same prep circuit, so one
  // /simulate with shots=N does it in a single call — keeping the single-worker
  // backend from being hammered with N sequential requests.
  const payload = {
    num_qubits: die.n,
    shots: dice,
    mode: quantum ? "quantum" : "qsim",
    gates: die.gates.map((g) => ({ name: GATES[g.label].m, qubits: [...g.qubits], param: g.param })),
  };
  try {
    const res = await fetch("/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    stopRollCycle();
    if (!res.ok) { setRollResult(name, null); return; }
    const data = await res.json();
    // Expand the counts tally into one face value per die. counts maps bitstring
    // -> how many of the N dice landed there; the face is the little-endian integer
    // + 1 so it reads like a physical die (1..N rather than 0..N-1).
    const results = [];
    for (const [bits, c] of Object.entries(data.counts || {})) {
      const face = parseInt(bits, 2) + 1;
      for (let i = 0; i < c; i++) results.push(face);
    }
    // For a single die, also show the exact statevector behind the roll — the
    // uniform superposition the die is prepared in — right inside the popup. (The
    // backend returns a locally-simulated statevector even in qsim mode.)
    setRollResult(name, results, dice === 1 ? data.statevector : null);
    recordRoll(name, results);
  } catch (_) {
    stopRollCycle();
    setRollResult(name, null);
  }
}

// How many dice a single click rolls (the "Dice" dropdown). Defaults to 1.
function rollCount() {
  const sel = $("roll-count");
  const n = sel ? parseInt(sel.value, 10) : 1;
  return Number.isFinite(n) && n > 0 ? n : 1;
}

// Flash random faces in the modal while a real-hardware roll is in flight.
function startRollCycle(faces) {
  clearInterval(rollCycleTimer);
  const faceEl = $("roll-face");
  rollCycleTimer = setInterval(() => {
    faceEl.innerHTML = `<span class="roll-val">${1 + Math.floor(Math.random() * faces)}</span>`;
  }, 75);
}
function stopRollCycle() {
  clearInterval(rollCycleTimer);
  rollCycleTimer = null;
}

// Show the roll modal in its "rolling…" state. The overlay is transparent and
// click-through (pointer-events:none in CSS) so it never blocks the UI.
function openRollModal(name, dice = 1) {
  clearTimeout(rollTimer);
  stopRollCycle();
  $("roll-die").textContent = dice > 1 ? `${dice} × ${name}` : name;
  $("roll-face").hidden = false;
  $("roll-face").innerHTML = `<span class="roll-val">…</span>`;
  $("roll-total").hidden = true;
  renderRollStatevector(null);
  renderRecent();
  const modal = $("roll-modal");
  modal.classList.remove("hidden", "fade");
}

// Reveal the rolled face value(s) — the result is the headline of the modal. One
// big number per die, plus a Total when several dice were rolled; then hold and
// fade. `results` is the per-die face array, or null on a failed roll.
function setRollResult(name, results, statevector = null) {
  const faceEl = $("roll-face");
  const totalEl = $("roll-total");
  faceEl.hidden = false;
  if (!results || !results.length) {
    faceEl.innerHTML = `<span class="roll-val">✕</span>`;
    totalEl.hidden = true;
    renderRollStatevector(null);
    scheduleRollFade(1500);
    return;
  }
  faceEl.innerHTML = results.map((f) => `<span class="roll-val">${f}</span>`).join("");
  if (results.length > 1) {
    totalEl.textContent = `Total ${results.reduce((a, b) => a + b, 0)}`;
    totalEl.hidden = false;
  } else {
    totalEl.hidden = true;
  }
  // Natural 20 on a d20 — the crit! Shoot fireworks out of the modal to celebrate.
  const nat20 = name === "d20" && results.some((f) => f === 20);
  if (nat20) launchFireworks();
  // Single die: reveal the statevector behind the roll and hold the popup longer
  // so it's readable. Multiple dice: counts-only, no statevector. Hold a beat
  // longer on a nat 20 so the fireworks finish before the modal fades.
  const showSv = results.length === 1 && renderRollStatevector(statevector);
  scheduleRollFade(showSv ? 6000 : nat20 ? 3200 : results.length > 1 ? 2800 : 2200);
}

// Celebratory particle burst for a natural 20. Pure DOM/CSS — spawns short-lived
// dots that fly outward from the centre of the roll card in a couple of staggered
// waves, then clean themselves up. No dependencies, nothing persistent.
function launchFireworks() {
  const modal = $("roll-modal");
  if (!modal) return;
  const card = modal.querySelector(".roll-card") || modal;
  card.classList.add("nat20");
  const colors = ["#ffd34e", "#ff5e8a", "#5ee0ff", "#8aff7a", "#c08bff", "#ff9a3d"];
  const burst = (count, spread, delay) => {
    const host = document.createElement("div");
    host.className = "fireworks";
    card.appendChild(host);
    for (let i = 0; i < count; i++) {
      const p = document.createElement("span");
      p.className = "fw";
      const ang = (Math.PI * 2 * i) / count + Math.random() * 0.4;
      const dist = spread * (0.6 + Math.random() * 0.6);
      p.style.setProperty("--dx", `${Math.cos(ang) * dist}px`);
      p.style.setProperty("--dy", `${Math.sin(ang) * dist}px`);
      p.style.background = colors[(i + Math.floor(Math.random() * colors.length)) % colors.length];
      p.style.animationDelay = `${delay + Math.random() * 0.12}s`;
      host.appendChild(p);
    }
    setTimeout(() => host.remove(), 1500 + delay * 1000);
  };
  burst(30, 150, 0);
  burst(22, 110, 0.22);
  setTimeout(() => card.classList.remove("nat20"), 1600);
}

// Render the die's statevector as a compact table inside the roll popup. Returns
// true if it drew something (so the caller can hold the popup open longer).
function renderRollStatevector(sv) {
  const el = $("roll-sv");
  if (!el) return false;
  if (!sv || !sv.length) { el.hidden = true; el.innerHTML = ""; return false; }
  const rows = sv
    .map((s) =>
      `<tr><td class="mono">|${s.basis}⟩</td>` +
      `<td class="mono">${s.re.toFixed(2)} ${s.im >= 0 ? "+" : "−"} ${Math.abs(s.im).toFixed(2)}i</td>` +
      `<td class="mono">${s.prob.toFixed(3)}</td></tr>`)
    .join("");
  el.innerHTML =
    `<div class="roll-sv-title">Statevector behind the roll</div>` +
    `<table><thead><tr><th>Basis</th><th>Amplitude</th><th>Prob</th></tr></thead><tbody>${rows}</tbody></table>`;
  el.hidden = false;
  return true;
}

// Hold the modal for `hold` ms, then fade it out (CSS opacity transition).
function scheduleRollFade(hold) {
  const modal = $("roll-modal");
  modal.classList.remove("fade");
  void modal.offsetWidth; // restart the transition if a previous roll is mid-fade
  clearTimeout(rollTimer);
  rollTimer = setTimeout(() => {
    modal.classList.add("fade");
    rollTimer = setTimeout(() => modal.classList.add("hidden"), 450);
  }, hold);
}

// ---- Roll history ----------------------------------------------------------
// The last few rolls, shown inside the roll modal (newest first). Each entry is
// one roll action's face values — rolls never touch the circuit on the canvas.
const ROLL_HISTORY_MAX = 5;
const rollHistory = [];

function recordRoll(name, results) {
  rollHistory.unshift({ name, faces: results || [] });
  if (rollHistory.length > ROLL_HISTORY_MAX) rollHistory.length = ROLL_HISTORY_MAX;
  renderRecent();
}

// One history line, e.g. "d6 → 4" or "3 × d6 → 4, 2, 6 (12)".
function rollSummaryText(r) {
  if (!r.faces.length) return `${r.name} → ✕`;
  if (r.faces.length === 1) return `${r.name} → ${r.faces[0]}`;
  const total = r.faces.reduce((a, b) => a + b, 0);
  return `${r.faces.length} × ${r.name} → ${r.faces.join(", ")} (${total})`;
}

function renderRecent() {
  const el = $("roll-recent");
  if (!el) return;
  if (!rollHistory.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML =
    `<div class="roll-recent-title">Recent rolls</div>` +
    rollHistory.map((r) => `<div class="roll-recent-row">${escapeAttr(rollSummaryText(r))}</div>`).join("");
}
