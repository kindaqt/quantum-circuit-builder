// ---- AI circuit explainer --------------------------------------------------
// The professor: personas, providers/models, the running conversation, and the
// /explain dispatch. loadConfig() also lives here — it reads /config to learn
// which optional features (quantum, AI providers, personas) the backend has on
// and wires the matching UI. Voices and API keys never leave the backend; the
// client only ever echoes back a validated persona/provider/model key.
//
// The professor only runs in two situations, never on passive edits: (1) on
// demand when you click "Explain this circuit", or (2) automatically right after
// you run a circuit on quantum hardware (the Run button) when auto-explain is on.
// We fingerprint the circuit so an unchanged circuit isn't re-explained on a
// repeat run — that would just spend an API call to print the same answer.

// The professor's personas come from /config (key + display name + blurb); the
// *voices* live server-side, so the client only ever sends back a key. Avatars are
// hand-built SVGs in avatars.js, keyed by persona key. Until config responds we
// fall back to a single default so the UI never breaks.
let personas = [{ key: "professor", name: "The Professor" }];
let currentPersona = "professor";

// The enabled AI providers (from /config), each { key, label, models[], default_model }.
// The student picks provider + model in the header; the server validates both, and
// keys never leave the backend. Empty until /config reports what's actually on.
let aiProviders = [];
let currentProvider = null;
let currentModel = null;

// Look up a provider record by key (falls back to the first enabled provider).
function providerInfo(key) {
  return aiProviders.find((p) => p.key === key) || aiProviders[0] || null;
}

// Look up a persona record by key (falls back to the first known persona).
function personaInfo(key) {
  return personas.find((p) => p.key === key) || personas[0];
}

// The avatar shown in the header and on each professor bubble. Each persona has a
// hand-built inline SVG in avatars.js (PERSONA_AVATARS, keyed by persona key); if a
// key is missing we fall back to DEFAULT_AVATAR. Only if avatars.js failed to load
// at all do we degrade to a plain name-initial badge so the UI never breaks.
// aria-label carries the name for screen readers.
function avatarHtml(key = currentPersona) {
  const p = personaInfo(key);
  const map = typeof PERSONA_AVATARS !== "undefined" ? PERSONA_AVATARS : null;
  const fallback = typeof DEFAULT_AVATAR !== "undefined" ? DEFAULT_AVATAR : null;
  const svg = (map && map[p.key]) || fallback || null;
  if (svg) {
    return `<span class="avatar-svg" role="img" aria-label="${escapeAttr(p.name)}">${svg}</span>`;
  }
  const initial = escapeHtml((p.name || "?").trim().charAt(0) || "?");
  return `<span class="avatar-initial" role="img" aria-label="${escapeAttr(p.name)}">${initial}</span>`;
}

let explaining = false;
let lastExplainedSig = null;
// The running conversation with the professor: alternating {role, content} turns
// (user content is the displayed question; assistant content is the answer). Sent
// back as `history` on each request so the professor remembers the discussion.
let conversation = [];

const OVERVIEW_PROMPT = "Please walk me through this circuit.";

// A stable signature of the current circuit (qubit count + ordered gates). Two
// circuits that look identical produce the same string.
function circuitSig() {
  return JSON.stringify({
    n: state.numQubits,
    init: [...state.initialStates],
    gates: state.gates.map((g) => [g.label, g.qubits, g.param]),
  });
}

// A small clickable snapshot of the current circuit, shown in the professor's
// "reading your circuit" bubble so you can see exactly what's being explained.
// Built from the same SVG the export/queue pictures use and inlined as a data URL
// (no async rasterization); clicking it opens the near-fullscreen circuit view.
function circuitThumbImg(src) {
  return `<img class="prof-circuit-thumb" src="${src}" ` +
    `alt="The circuit being explained" title="Click to enlarge" />`;
}
function circuitThumbHtml() {
  return circuitThumbImg(svgDataUrl(circuitSVG().svg));
}

// A data-URL snapshot of the current circuit, captured at explain time so the
// thumbnail stored on a conversation turn stays faithful even if the canvas is
// edited afterward.
function circuitSnapshotUrl() {
  return svgDataUrl(circuitSVG().svg);
}

// Wall-clock time for a conversation turn, e.g. "2:34:56 PM". Empty for older
// turns that predate timestamps. (queueTimestamp lives in queue.js; reuse it.)
function explainTimeHtml(ts) {
  const stamp = typeof queueTimestamp === "function" ? queueTimestamp(ts) : "";
  return stamp ? `<span class="explain-time">${stamp}</span>` : "";
}

// One answer's worth of text -> paragraph HTML. Each paragraph is run through
// renderRichText (latex.js), which escapes the prose and typesets any inline/
// display TeX math the professor uses (kets, fractions, tensor products, …).
function renderParagraphs(text) {
  return text
    .split(/\n\s*\n/)
    .map((p) => `<p>${renderRichText(p)}</p>`)
    .join("");
}

// Paint the whole conversation into #explain-out. `pending`, if given, appends a
// transient professor bubble (e.g. "thinking…") below the committed turns.
function renderConversation(pending) {
  const out = $("explain-out");
  if (!out) return;
  if (!conversation.length && !pending) {
    out.innerHTML = `<div class="hint">Run a circuit, press “Explain this circuit”, or ask the professor a question to get a plain-language answer grounded in your circuit.</div>`;
    return;
  }
  let html = "";
  for (const turn of conversation) {
    if (turn.role === "user") {
      html += `<p class="explain-question"><strong>You asked:</strong> ` +
        `${escapeHtml(turn.content)}${explainTimeHtml(turn.ts)}</p>`;
    } else {
      // Keep the snapshot of the circuit this answer was about, so the picture
      // stays in the history (not just in the transient "reading…" bubble).
      const thumb = turn.circuit ? circuitThumbImg(turn.circuit) : "";
      html += `<div class="prof-msg"><span class="prof-avatar">${avatarHtml(turn.persona)}</span>` +
        `<div class="prof-bubble">${renderParagraphs(turn.content)}${thumb}` +
        `${explainTimeHtml(turn.ts)}</div></div>`;
    }
  }
  if (pending) {
    // While the professor "reads", show a thumbnail of the exact circuit being
    // explained (only when there's something on the canvas). Click it to enlarge.
    const thumb = hasContent() ? circuitThumbHtml() : "";
    html += `<div class="prof-msg"><span class="prof-avatar">${avatarHtml()}</span><div class="prof-bubble"><span class="hint">${escapeHtml(pending)}</span>${thumb}</div></div>`;
  }
  out.innerHTML = html;
  out.scrollTop = out.scrollHeight; // keep the newest message in view
}

// Wipe the conversation and start fresh.
function clearConversation() {
  conversation = [];
  lastExplainedSig = null;
  renderConversation();
}

// Auto-trigger hook called after an explicit run. Bails out unless the feature
// is on, the toggle is checked, there's something to explain, and the circuit
// actually changed since the last explanation.
function maybeAutoExplain() {
  const auto = $("explain-auto");
  if ($("tab-explain").hidden) return;     // AI feature disabled by the backend
  if (!auto || !auto.checked) return;      // user opted out of auto-explain
  if (!hasContent()) return;               // nothing meaningful to explain
  if (circuitSig() === lastExplainedSig) return; // already explained this exact circuit
  explainCircuit();
}

// Ask the professor. With no `question` this is the overview walkthrough (and it
// needs gates to be meaningful); with a question the student can ask about even
// an empty canvas, so we only gate the overview path on having gates.
async function explainCircuit(question = null) {
  if (explaining) return;
  // The overview runs even on a blank canvas — explaining an empty circuit (all
  // qubits in |0…0⟩, nothing applied) is a legitimate starting lesson.
  explaining = true;
  const btn = $("explain-btn");
  const askBtn = $("explain-ask-btn");
  // The explainer can take 10+ seconds (a large model is thinking). Make that
  // visible so the button doesn't look dead: disable it and swap in a "Thinking…"
  // label until the answer (or an error) lands.
  const btnLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Thinking…";
  btn.classList.add("is-busy");
  if (askBtn) askBtn.disabled = true;
  // Show the question immediately, with a "thinking" bubble underneath.
  const userTurn = { role: "user", content: question || OVERVIEW_PROMPT, ts: Date.now() };
  // Snapshot the circuit now so the answer keeps a faithful picture even if the
  // canvas is edited during the (10+ second) wait. Empty canvas → no picture.
  const circuitShot = hasContent() ? circuitSnapshotUrl() : null;
  if (question) conversation.push(userTurn); // surface the question right away
  renderConversation(question ? "Thinking…" : "Reading your circuit…");
  if (question) conversation.pop();           // re-added on success below
  const payload = {
    num_qubits: state.numQubits,
    shots: state.shots,
    mode: "sim",
    gates: gatePayload(),
    // Only role+content go to the model — strip the per-turn picture/timestamp.
    history: conversation.map((t) => ({ role: t.role, content: t.content })),
    persona: currentPersona,          // which voice answers (server validates the key)
    provider: currentProvider,        // which AI provider runs it (server validates)
    model: currentModel,              // which model (server validates against the provider)
  };
  if (question) payload.question = question;
  try {
    const res = await fetch("/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      renderConversation();
      $("explain-out").insertAdjacentHTML(
        "beforeend",
        `<div class="hint">${escapeHtml(err.detail || "The explainer is unavailable right now.")}</div>`,
      );
      return;
    }
    const data = await res.json();
    // Commit both sides of this exchange to the running conversation. Tag the
    // answer with the persona that produced it so its bubble keeps the right
    // avatar even after the student switches personas later.
    conversation.push(userTurn, {
      role: "assistant",
      content: data.explanation,
      persona: data.persona || currentPersona,
      circuit: circuitShot,   // the circuit snapshot this answer is about
      ts: Date.now(),
    });
    renderConversation();
    // Only the overview path dedupes against the circuit signature; a question is
    // always sent (the student may ask several things about the same circuit).
    if (!question) lastExplainedSig = circuitSig();
  } catch (err) {
    renderConversation();
    $("explain-out").insertAdjacentHTML(
      "beforeend",
      `<div class="hint">Couldn't reach the explainer: ${escapeHtml(String(err))}</div>`,
    );
  } finally {
    btn.textContent = btnLabel;
    btn.classList.remove("is-busy");
    btn.disabled = false; // stays clickable; an empty-canvas click shows a hint
    if (askBtn) askBtn.disabled = false;
    explaining = false;
  }
}

// Pull the question out of the input, send it, and clear the box on success.
function askProfessor() {
  const input = $("explain-q");
  if (!input) return;
  const q = input.value.trim();
  if (!q || explaining) return;
  input.value = "";
  explainCircuit(q);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function loadConfig() {
  try {
    const res = await fetch("/config");
    if (!res.ok) return;
    const cfg = await res.json();
    // Align the qubit cap with what the backend will actually simulate, then
    // re-render so the +qubit button enables/disables against the real limit.
    if (Number.isInteger(cfg.max_qubits) && cfg.max_qubits > 0) {
      MAX_QUBITS = cfg.max_qubits;
      render();
    }
    // The Queue tab logs every run — the live classical sim as well as any
    // hardware jobs — so it's always available. Seed its pending cap from the
    // backend (QCB_QUEUE_MAX) and paint its empty state.
    $("tab-queue").hidden = false;
    setQueueMax(Number.isInteger(cfg.queue_max) && cfg.queue_max > 0 ? cfg.queue_max : QUEUE_MAX);
    // The run controls are always shown: the Run button lets the student snapshot
    // the current classical result into the queue. The "Run on" picker only matters
    // when real hardware is configured, so reveal it (and label its backend) then.
    $("run-mode-ctrl").hidden = false;
    if (cfg.quantum_enabled) {
      $("run-on-pick").hidden = false;
      const opt = document.querySelector('#run-mode option[value="quantum"]');
      if (opt && cfg.backend) opt.textContent = `Quantum hardware (${cfg.backend})`;
    }
    updateRunAvailability(); // set the Run button's tooltip/enabled state for the current mode
    // The AI Explain tab only exists when the backend reports the feature on
    // (flag set + key present). Without it the tab/panel stay hidden.
    if (cfg.ai_enabled) {
      $("tab-explain").hidden = false;
      if (Array.isArray(cfg.personas) && cfg.personas.length) {
        personas = cfg.personas;
        currentPersona = cfg.default_persona && personaInfo(cfg.default_persona).key === cfg.default_persona
          ? cfg.default_persona
          : personas[0].key;
      }
      // Enabled AI providers + their models — the student can switch both.
      if (Array.isArray(cfg.ai_providers) && cfg.ai_providers.length) {
        aiProviders = cfg.ai_providers;
        currentProvider = cfg.default_provider && providerInfo(cfg.default_provider)
          ? cfg.default_provider
          : aiProviders[0].key;
        const dp = providerInfo(currentProvider);
        currentModel = (dp && dp.default_model) || (dp && dp.models && dp.models[0]) || null;
      }
      populatePersonaMenu();
      applyPersona(currentPersona);
      populateProviderSelect();
      populateModelSelect();
      applyModel(currentModel || cfg.ai_model);
      // The professor leads: make it the default tab whenever the feature is on.
      activateTab("explain");
    }
  } catch (_) { /* config is best-effort; app still works as a classical sim */ }
}

// Personas in menu order: the default Professor pinned to the top, the rest
// alphabetical by display name.
function sortedPersonas() {
  const rest = personas
    .filter((p) => p.key !== "professor")
    .sort((a, b) => a.name.localeCompare(b.name));
  const prof = personas.find((p) => p.key === "professor");
  return prof ? [prof, ...rest] : rest;
}

// Build the custom persona dropdown: each row shows the persona's SVG avatar +
// name, with the who-is-this blurb as a hover tooltip. A native <select> can't
// render SVGs, so this is a lightweight listbox we drive ourselves.
function populatePersonaMenu() {
  const menu = $("persona-menu");
  if (!menu) return;
  menu.innerHTML = sortedPersonas()
    .map((p) => {
      const sel = p.key === currentPersona ? " is-active" : "";
      const tip = p.blurb ? ` title="${escapeAttr(p.blurb)}"` : "";
      return `<div class="persona-opt${sel}" role="option" data-key="${escapeAttr(p.key)}"`
        + ` aria-selected="${p.key === currentPersona}"${tip}>`
        + `<span class="persona-opt-av" aria-hidden="true">${avatarHtml(p.key)}</span>`
        + `<span class="persona-opt-name">${escapeHtml(p.name)}</span></div>`;
    })
    .join("");
}

// Open/close the persona menu.
function setPersonaMenuOpen(open) {
  const menu = $("persona-menu");
  const trigger = $("persona-trigger");
  if (!menu || !trigger) return;
  menu.hidden = !open;
  trigger.setAttribute("aria-expanded", String(open));
  if (open) {
    populatePersonaMenu();
    const active = menu.querySelector(".persona-opt.is-active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }
}
function togglePersonaMenu() {
  setPersonaMenuOpen($("persona-menu") && $("persona-menu").hidden);
}

// Reflect the active persona in the header (avatar + name + who-is-this tooltip)
// and in the dropdown trigger; bubbles keep the persona they were generated with.
function applyPersona(key) {
  const p = personaInfo(key);
  currentPersona = p.key;
  const slot = $("prof-avatar-slot");
  if (slot) {
    slot.innerHTML = avatarHtml();
    // The styled tooltip (attachTip in init) reads the live persona blurb on hover,
    // so we don't set a native title here — that would double up with our own tip.
    slot.removeAttribute("title");
  }
  const nameEl = $("prof-name");
  if (nameEl) nameEl.textContent = p.name;
  // Mirror into the dropdown trigger.
  const trigAv = $("persona-trigger-av");
  if (trigAv) trigAv.innerHTML = avatarHtml(p.key);
  const trigName = $("persona-trigger-name");
  if (trigName) trigName.textContent = p.name;
  const trigger = $("persona-trigger");
  if (trigger && p.blurb) trigger.title = p.blurb;
}

// Pick a persona from the menu: apply it and close.
function choosePersona(key) {
  applyPersona(key);
  setPersonaMenuOpen(false);
  $("persona-trigger") && $("persona-trigger").focus();
}

// Fill the provider dropdown from the enabled list and select the active one.
function populateProviderSelect() {
  const sel = $("provider-select");
  if (!sel) return;
  sel.innerHTML = aiProviders
    .map((p) => `<option value="${escapeAttr(p.key)}">${escapeHtml(p.label)}</option>`)
    .join("");
  if (currentProvider) sel.value = currentProvider;
  // A single provider needs no chooser — hide its label to reduce clutter.
  const label = sel.closest(".persona-pick");
  if (label) label.hidden = aiProviders.length < 2;
}

// Fill the model dropdown from the active provider's model list.
function populateModelSelect() {
  const sel = $("model-select");
  if (!sel) return;
  const p = providerInfo(currentProvider);
  const models = (p && p.models) || [];
  sel.innerHTML = models
    .map((m) => `<option value="${escapeAttr(m)}">${escapeHtml(m)}</option>`)
    .join("");
  if (currentModel && models.includes(currentModel)) sel.value = currentModel;
  const label = sel.closest(".persona-pick");
  if (label) label.hidden = models.length < 2;
}

// Switch provider: adopt its default model, refresh the model dropdown + label.
function applyProvider(key) {
  const p = providerInfo(key);
  if (!p) return;
  currentProvider = p.key;
  currentModel = p.default_model || (p.models && p.models[0]) || null;
  populateModelSelect();
  applyModel(currentModel);
}

// Switch model: remember it and reflect it in the "via …" subline.
function applyModel(model) {
  currentModel = model || currentModel;
  const el = $("explain-model");
  if (el) el.textContent = currentModel ? `via ${currentModel}` : "";
}
