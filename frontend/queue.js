// ---- Run queue -------------------------------------------------------------
// A log of circuit runs, shown in the "Queue" tab. Both the classical live sim and
// real quantum-hardware runs land here as cards: each shows a PNG of the circuit
// that ran and (when finished) its measured histogram. Quantum-hardware jobs queue
// for minutes, so at most QUEUE_MAX of them may be *pending* (in flight) at once —
// at the cap the Run button is disabled with a tooltip. We keep the HISTORY_MAX
// most-recent finished runs (newest first) and drop older ones; pending runs are
// always kept regardless of the history cap.
let QUEUE_MAX = 2;          // pending-run cap, overwritten from /config (QCB_QUEUE_MAX)
const HISTORY_MAX = 10;     // how many finished runs to keep
let queueSeq = 0;           // monotonically increasing run number
const runQueue = [];        // newest first: { id, kind, status, png, sig, n, shots, counts, backend, detail }

// How many runs are currently in flight (Pending). Only these count toward the
// pending cap — completed/failed cards stay as history but free their slot.
function pendingRuns() {
  return runQueue.filter((r) => r.status === "pending").length;
}

// Keep every pending run plus at most HISTORY_MAX finished (done/failed) runs,
// newest first; drop the oldest finished runs beyond the cap.
function trimQueue() {
  let finished = 0;
  const kept = [];
  for (const r of runQueue) {
    if (r.status === "pending") { kept.push(r); continue; }
    if (finished < HISTORY_MAX) { kept.push(r); finished++; }
  }
  runQueue.length = 0;
  runQueue.push(...kept);
}

// Record a finished classical (local-sim) run in the queue. Classical runs are
// instant, so they go straight to "done" — no pending card. Re-running the exact
// same circuit (e.g. only the shot count changed) updates the most recent classical
// card in place instead of stacking a duplicate; a genuinely different circuit gets
// its own card. `snap` is the circuit SVG captured at run time, for the card PNG.
function recordClassicalRun(snap, n, shots, sig, data) {
  const head = runQueue[0];
  if (head && head.kind === "classical" && head.sig === sig) {
    head.counts = data.counts;
    head.backend = data.backend;
    head.shots = shots;
    head.ts = Date.now(); // re-running bumps the card's timestamp
    renderQueue();
    return;
  }
  const entry = {
    id: ++queueSeq, kind: "classical", status: "done", png: null, sig,
    n, shots, counts: data.counts, backend: data.backend, detail: "", ts: Date.now(),
  };
  runQueue.unshift(entry);
  trimQueue();
  renderQueue();
  // Rasterize the snapshot for the card PNG (async, non-blocking).
  svgToPng(snap.svg, snap.W, snap.H, 2)
    .then(({ dataUrl }) => { entry.png = dataUrl; renderQueue(); })
    .catch(() => { /* leave the placeholder if rasterization fails */ });
}

// Apply the configured cap (called from loadConfig once /config is known).
function setQueueMax(n) {
  if (Number.isFinite(n) && n > 0) QUEUE_MAX = Math.floor(n);
  renderQueue();
  updateRunAvailability();
}

// The Run button is enabled only in quantum mode and only while fewer than
// QUEUE_MAX runs are pending; otherwise it's disabled with an explanatory tip.
function updateRunAvailability() {
  const btn = $("run-hw");
  if (!btn) return;
  if (state.runMode !== "quantum") {
    // Classical mode: Run is always available — it logs the live result to the queue.
    btn.disabled = false;
    btn.title = "Add this circuit and its result to the queue";
    return;
  }
  const pend = pendingRuns();
  const full = pend >= QUEUE_MAX;
  btn.disabled = full;
  btn.title = full
    ? `Queue is full — ${pend} run${pend === 1 ? "" : "s"} already pending (max ${QUEUE_MAX}). ` +
      `Wait for a run to finish before submitting another.`
    : "Submit this circuit to quantum hardware";
}

// Submit the current circuit as a real-hardware run and track it in the queue.
// Called by runSim() for quantum mode. We snapshot the circuit synchronously (it
// can be edited while the run is pending) for both the job payload and the card
// picture, so the card always shows exactly what was submitted.
async function submitQuantumRun() {
  if (pendingRuns() >= QUEUE_MAX) return; // guard; the button should already be disabled
  const gates = gatePayload();            // circuit snapshot for the job
  const snap = circuitSVG();              // circuit snapshot for the picture
  const entry = {
    id: ++queueSeq, kind: "quantum", status: "pending", png: null, sig: null,
    n: state.numQubits, shots: state.shots, counts: null, backend: null, detail: "",
    ts: Date.now(),
  };
  runQueue.unshift(entry);
  renderQueue();
  updateRunAvailability();
  setRunStatus(`Submitted — ${pendingRuns()} in queue`, "busy");
  activateTab("queue"); // surface the queue so the user sees their pending run

  // Rasterize the snapshot for the card (async — the canvas may change meanwhile,
  // but `snap` was taken at submit time, so the picture stays faithful).
  try {
    const { dataUrl } = await svgToPng(snap.svg, snap.W, snap.H, 2);
    entry.png = dataUrl;
    renderQueue();
  } catch (_) { /* leave the placeholder if rasterization fails */ }

  try {
    const res = await fetch("/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ num_qubits: entry.n, shots: entry.shots, mode: "quantum", gates }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      entry.status = "failed";
      entry.detail = err.detail || "Run rejected.";
    } else {
      const data = await res.json();
      entry.status = "done";
      entry.counts = data.counts;
      entry.backend = data.backend;
      renderResults(data);  // also reflect the latest completed run in the main tabs
      maybeAutoExplain();   // a hardware Run is an explicit run — explain it if enabled
    }
  } catch (err) {
    entry.status = "failed";
    entry.detail = "Run error: " + err;
  } finally {
    trimQueue();
    renderQueue();
    updateRunAvailability();
    const pend = pendingRuns();
    if (entry.status === "done") {
      setRunStatus(`Ran on ${entry.backend}` + (pend ? ` · ${pend} still in queue` : ""), "");
    } else if (pend === 0) {
      setRunStatus("Run failed", "err");
    }
  }
}

// A compact histogram of one run's measured counts, as an HTML string for the card.
function queueHistogramHtml(counts) {
  const entries = histOrderedEntries(counts);
  const max = Math.max(1, ...entries.map(([, c]) => c));
  const bars = entries.map(([k, c]) =>
    `<div class="hbar">` +
      `<div class="cnt">${c}</div>` +
      `<div class="bar" style="height:${(c / max) * 100}%"></div>` +
      `<div class="lbl">|${escapeAttr(k)}⟩</div>` +
    `</div>`).join("");
  return `<div class="queue-hist histogram">${bars}</div>`;
}

// Format a run's wall-clock time for its card, e.g. "2:34:56 PM". Falls back to
// an empty string for older entries that predate timestamps.
function queueTimestamp(ts) {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString(undefined, {
      hour: "numeric", minute: "2-digit", second: "2-digit",
    });
  } catch (_) {
    return "";
  }
}

// One run card: header + status, the submitted-circuit picture, and (when done)
// the measured results or (when failed) the error detail.
function runCardHtml(r) {
  const kindLabel = r.kind === "classical" ? "Classical" : "Quantum";
  const head = `Run #${r.id} · ${kindLabel} · ${r.n} qubit${r.n === 1 ? "" : "s"} · ${r.shots} shots`;
  const stamp = queueTimestamp(r.ts);
  const statusText =
    r.status === "pending" ? "Pending…" :
    r.status === "done"    ? `Completed${r.backend ? " on " + escapeAttr(r.backend) : ""}` :
                             "Failed";
  const pic = r.png
    ? `<div class="qrun-circuit-wrap">` +
        `<img class="qrun-circuit" src="${r.png}" alt="Circuit submitted for run ${r.id}"/>` +
      `</div>`
    : `<div class="qrun-circuit-ph">Rendering circuit…</div>`;
  let body;
  if (r.status === "done" && r.counts) {
    body = `<div class="qrun-results">${queueHistogramHtml(r.counts)}</div>`;
  } else if (r.status === "failed") {
    body = `<div class="qrun-detail err">${escapeAttr(r.detail || "Run rejected.")}</div>`;
  } else {
    body = `<div class="qrun-detail muted">Waiting for the device to return measurement counts…</div>`;
  }
  return `<div class="qrun qrun-${r.status}">` +
    `<div class="qrun-head"><span class="qrun-title">${head}</span>` +
    (stamp ? `<span class="qrun-time">${stamp}</span>` : "") +
    `<span class="qrun-status qrun-status-${r.status}">${statusText}</span></div>` +
    pic + body + `</div>`;
}

function renderQueue() {
  const el = $("queue");
  if (!el) return;
  if (!runQueue.length) {
    el.innerHTML =
      `<div class="hint">Every run shows up here — the live classical simulation as you edit, ` +
      `and any <strong>Run on → Quantum hardware</strong> jobs you submit. Each card shows the ` +
      `circuit that ran and its measured histogram; quantum jobs appear as <em>Pending</em> first, ` +
      `then fill in when the device returns. Up to ${QUEUE_MAX} hardware runs can be in flight at ` +
      `once, and the ${HISTORY_MAX} most-recent finished runs are kept.</div>`;
    return;
  }
  el.innerHTML = runQueue.map(runCardHtml).join("");
}
