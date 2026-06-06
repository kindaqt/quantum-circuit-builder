// ---- Wiring & init ---------------------------------------------------------
// Loaded last: every other module has defined its functions in the shared global
// scope by now, so this file binds the toolbar/keyboard/pointer event handlers
// and kicks off the first render. Keep DOM event wiring here rather than scattered
// across the feature modules, so the app's entry points live in one place.

// ---- Toolbar wiring --------------------------------------------------------
$("q-minus").addEventListener("click", () => setQubits(state.numQubits - 1));
$("q-plus").addEventListener("click", () => setQubits(state.numQubits + 1));
$("shots").addEventListener("change", (e) => { state.shots = +e.target.value; scheduleSim(); });
$("undo").addEventListener("click", () => { state.gates.pop(); update(); });
$("clear").addEventListener("click", () => {
  state.gates = [];
  state.initialStates = state.initialStates.map(() => 0);
  update();
});
$("export").addEventListener("click", openExport);
$("export-close").addEventListener("click", closeExport);
$("export-backdrop").addEventListener("click", closeExport);
$("export-png-download").addEventListener("click", downloadCircuitPng);
document.querySelectorAll(".export-copy").forEach((b) =>
  b.addEventListener("click", () => copyExport(b)));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("export-modal").classList.contains("hidden")) closeExport();
});
document.querySelectorAll("[data-hist-mode]").forEach((b) =>
  b.addEventListener("click", () => setHistOption("mode", b)));
document.querySelectorAll("[data-hist-sort]").forEach((b) =>
  b.addEventListener("click", () => setHistOption("sort", b)));
$("hist-png").addEventListener("click", downloadHistogramPng);
$("hist-csv").addEventListener("click", downloadHistogramCsv);
$("run-mode").addEventListener("change", (e) => setRunMode(e.target.value));
$("run-hw").addEventListener("click", () => runSim(state.runMode, { toQueue: true }));
$("explain-btn").addEventListener("click", () => explainCircuit());
$("explain-ask-btn").addEventListener("click", askProfessor);
$("explain-clear").addEventListener("click", clearConversation);
// Custom persona dropdown: trigger toggles the menu; a click on a row selects it;
// clicking outside or pressing Escape closes it.
$("persona-trigger").addEventListener("click", (e) => { e.stopPropagation(); togglePersonaMenu(); });
$("persona-menu").addEventListener("click", (e) => {
  const opt = e.target.closest(".persona-opt");
  if (opt) choosePersona(opt.getAttribute("data-key"));
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#persona-combo")) setPersonaMenuOpen(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("persona-menu") && !$("persona-menu").hidden) {
    setPersonaMenuOpen(false);
    $("persona-trigger").focus();
  }
});
// Click the circuit thumbnail in a professor "reading your circuit" bubble to open
// the near-fullscreen circuit view (delegated, since the bubble is re-rendered).
$("explain-out").addEventListener("click", (e) => {
  const thumb = e.target.closest(".prof-circuit-thumb");
  if (thumb) openCircuitModal(thumb.src);
});
// Click a queue card's circuit picture to open the same enlarged view (delegated,
// since the queue is re-rendered as runs land). Skip the "Rendering…" placeholder.
$("queue").addEventListener("click", (e) => {
  const pic = e.target.closest(".qrun-circuit");
  if (pic && pic.src) openCircuitModal(pic.src);
});
$("circuit-modal-close").addEventListener("click", closeCircuitModal);
$("circuit-modal-backdrop").addEventListener("click", closeCircuitModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("circuit-modal").classList.contains("hidden")) closeCircuitModal();
});

// General error modal: dismiss via OK, backdrop, or Escape.
$("error-modal-ok").addEventListener("click", closeErrorModal);
$("error-modal-backdrop").addEventListener("click", closeErrorModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("error-modal").classList.contains("hidden")) closeErrorModal();
});

// Catch-all: anything that bubbles up here had no dedicated place to be shown
// (an unexpected exception or an unhandled promise rejection), so surface it in
// the general error modal. Guarded so it never stacks on itself, and we ignore
// benign resource-load "error" events (those carry no Error object).
function errorModalOpen() {
  const m = $("error-modal");
  return m && !m.classList.contains("hidden");
}
window.addEventListener("unhandledrejection", (e) => {
  const reason = e && e.reason;
  if (!reason || (reason.name === "AbortError")) return;
  if (errorModalOpen()) return;
  showError((reason && reason.message) || String(reason), "Unexpected error");
});
window.addEventListener("error", (e) => {
  if (!e || !e.error) return;          // skip resource-load errors (img/script/etc.)
  if (errorModalOpen()) return;
  showError(e.message || String(e.error), "Unexpected error");
});
$("provider-select").addEventListener("change", (e) => applyProvider(e.target.value));
$("model-select").addEventListener("change", (e) => applyModel(e.target.value));
$("explain-q").addEventListener("keydown", (e) => {
  // Enter sends; Shift+Enter inserts a newline (standard chat behaviour for a textarea).
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askProfessor(); }
});

// Results tabs: clicking a tab activates its panel.
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
});

// Pure layout on resize — must NOT re-sample the simulation.
window.addEventListener("resize", render);

// Keep the qubit labels / init-state kets / "+ qubit" button frozen at the left
// edge while the circuit scrolls horizontally: --sx counter-translates them by
// the scroll offset (see .qlabel / .qgutter / .add-line in style.css).
scroll.addEventListener("scroll", () => {
  inner.style.setProperty("--sx", scroll.scrollLeft + "px");
}, { passive: true });

// Drag the divider to retrade vertical space between the circuit and the
// results. We pin the circuit to an explicit height and let the results pane
// (flex:1) absorb the rest, clamped so neither collapses. This is pure layout —
// it never re-simulates (random measurement sampling must not change on resize).
(function initPaneResize() {
  const divider = $("pane-divider");
  if (!divider) return;
  const MIN_CIRCUIT = 80, MIN_RESULTS = 140;
  let dragging = false;
  divider.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    divider.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing-v");
  });
  divider.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const mRect = document.querySelector("main").getBoundingClientRect();
    const top = scroll.getBoundingClientRect().top;
    const maxH = mRect.bottom - top - MIN_RESULTS;
    const h = Math.max(MIN_CIRCUIT, Math.min(e.clientY - top, maxH));
    scroll.style.flex = "0 0 auto";
    scroll.style.maxHeight = "none";
    scroll.style.height = h + "px";
  });
  const stop = (e) => {
    if (!dragging) return;
    dragging = false;
    try { divider.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    document.body.classList.remove("resizing-v");
  };
  divider.addEventListener("pointerup", stop);
  divider.addEventListener("pointercancel", stop);
})();

// ---- Init ------------------------------------------------------------------
buildPalette();
update();
loadConfig();

// Styled tooltip on the top-left professor avatar: show the active persona's blurb
// (it changes when you pick a different persona, hence the getter).
attachTip($("prof-avatar-slot"), () => {
  const p = personaInfo(currentPersona);
  return p.blurb || p.name;
});

// Collapsible palettes: each row's label is a button that hides/shows its contents.
document.querySelectorAll(".palette-label").forEach((label) => {
  label.addEventListener("click", () => {
    const row = label.closest(".palette-row");
    if (!row) return;
    const collapsed = row.classList.toggle("collapsed");
    label.setAttribute("aria-expanded", String(!collapsed));
  });
});
