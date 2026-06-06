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

// ---- Learner identity (Tier 3b/c) ------------------------------------------
// A learner id is minted once (POST /learner) and stored in localStorage. It is
// sent on /explain (best-effort profile lookup) and all quiz/memory routes. When
// the DB is unavailable (memory_enabled=false from /config) the feature is not
// activated; the id is simply not created/sent.
let learnerId = null;
let memoryEnabled = false;  // set by loadConfig from /config
let aiEnabled = false;      // set by loadConfig from /config; gates handoff calls

// How many professor turns between automatic quiz triggers (from /config).
let quizInterval = 3;
// The quiz currently waiting for an answer, or null.
let activeQuiz = null;  // {quiz_id, question, topic}
// Are we currently fetching a quiz or grading an answer?
let quizzing = false;
// The stored learner profile row (from GET /learner/{id}).
let learnerProfile = null;

// ---- Lazy-loaded conversation history (from DB) --------------------------
// Interactions from past sessions are loaded on demand — 20 at a time, from
// newest-of-old back to oldest — so the browser never has to hold thousands
// of DOM nodes at once. historicalTurns are prepended to the rendered view;
// they are NOT sent as LLM history (they're context, loaded separately).
const HISTORY_PAGE_SIZE = 20;
let historicalTurns = [];    // loaded DB rows mapped to {role, content, persona, ts}
let historyOldestId = null;  // id of the oldest interaction loaded so far (cursor)
let historyHasMore = true;   // false once the server returns fewer than PAGE_SIZE rows
let historyLoading = false;  // guard against concurrent fetches

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

// A small clickable snapshot of the circuit, shown inside the student's chat
// bubble so the history records exactly what was being asked about. Clicking it
// opens the near-fullscreen circuit view. The picture stays with the student's
// turn, never on the professor's answer bubble.
function circuitThumbImg(src) {
  if (src === "loading") {
    // Placeholder shown while the canvas is being rasterized to a PNG. Replaced
    // by the real thumbnail once circuitSnapshotUrl() resolves at stream end.
    return `<div class="prof-circuit-loading" aria-label="Capturing circuit…">Capturing…</div>`;
  }
  return `<img class="prof-circuit-thumb" src="${src}" ` +
    `alt="The circuit being explained" title="Click to enlarge" />`;
}

// A data-URL snapshot of the current circuit, captured at explain time so the
// thumbnail stored on a conversation turn stays faithful even if the canvas is
// edited afterward. Rasterized to a real PNG (the same local svgToPng encoder the
// export and run-queue pictures use) rather than an inline SVG data URL: a
// `data:image/svg+xml` <img> source fails to decode in some browsers (notably
// Safari), which left the persona's circuit picture blank. A PNG renders
// everywhere. If rasterization fails, fall back to the SVG so we never drop the
// picture entirely.
async function circuitSnapshotUrl() {
  let svg, W, H;
  try {
    ({ svg, W, H } = circuitSVG());
  } catch (_) {
    return null;  // state not ready or gate spec missing — skip thumbnail
  }
  try {
    const { dataUrl } = await svgToPng(svg, W, H, 2);
    return dataUrl;
  } catch (_) {
    return svgDataUrl(svg);  // PNG failed; SVG data URL as fallback
  }
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

// Grade badge rendered in the quiz result turn: ✓ correct, ~ partial, ✗ incorrect.
function _gradeBadge(grade) {
  const map = {
    correct:   { cls: "grade-correct",   icon: "✓", label: "Correct" },
    partial:   { cls: "grade-partial",   icon: "~", label: "Partially correct" },
    incorrect: { cls: "grade-incorrect", icon: "✗", label: "Incorrect" },
  };
  const g = map[grade] || { cls: "grade-incorrect", icon: "?", label: "Unknown" };
  return `<span class="quiz-grade-badge ${g.cls}">${g.icon} ${g.label}</span>`;
}

// Paint the whole conversation into #explain-out. `pending`, if given, appends a
// transient professor bubble (e.g. "thinking…") below the committed turns.
function renderConversation(pending) {
  const out = $("explain-out");
  if (!out) return;
  const hasHistory = historicalTurns.length > 0;
  const hasCurrent = conversation.length > 0;
  if (!hasHistory && !hasCurrent && !pending) {
    out.innerHTML = `<div class="hint">Run a circuit, press "Explain this circuit", or ask the professor a question to get a plain-language answer grounded in your circuit.</div>`;
    return;
  }
  let html = "";

  // ---- Historical turns section (from DB, previous sessions) ----
  // A sentinel div at the very top triggers the scroll-up infinite loader.
  // While a page is fetching it becomes a "loading" label instead.
  if (historyHasMore && (hasHistory || hasCurrent || pending)) {
    html += historyLoading
      ? `<div class="history-loading"><span class="hint">Loading history…</span></div>`
      : `<div class="history-sentinel" aria-hidden="true"></div>`;
  }
  for (const t of historicalTurns) {
    if (t.role === "user") {
      html += `<div class="prof-msg user-msg">` +
        `<span class="prof-avatar user-avatar" role="img" aria-label="You">U</span>` +
        `<div class="prof-bubble user-bubble"><p>${escapeHtml(t.content)}</p>` +
        `${explainTimeHtml(t.ts)}</div></div>`;
    } else {
      html += `<div class="prof-msg"><span class="prof-avatar">${avatarHtml(t.persona)}</span>` +
        `<div class="prof-bubble">${renderParagraphs(t.content)}` +
        `${explainTimeHtml(t.ts)}</div></div>`;
    }
  }
  // Session divider between loaded history and the current session
  if (hasHistory && (hasCurrent || pending)) {
    html += `<div class="session-divider" role="separator"><span>This session</span></div>`;
  }
  // ---- End historical turns section ----
  for (const turn of conversation) {
    if (turn.role === "user") {
      // The student's turn is a chat bubble on the right (Claude-style), with a
      // plain "U" avatar. The circuit snapshot rides inside this bubble — it
      // records what they were asking about — never on the professor's answer.
      const thumb = turn.circuit ? circuitThumbImg(turn.circuit) : "";
      html += `<div class="prof-msg user-msg">` +
        `<span class="prof-avatar user-avatar" role="img" aria-label="You">U</span>` +
        `<div class="prof-bubble user-bubble"><p>${escapeHtml(turn.content)}</p>` +
        `${thumb}${explainTimeHtml(turn.ts)}</div></div>`;

    } else if (turn.role === "quiz") {
      // A quiz question from the professor: looks like a professor bubble but
      // with a ⚡ badge, and (if it is still awaiting an answer) an inline form.
      const isActive = activeQuiz && activeQuiz.quiz_id === turn.quiz_id;
      const badge = `<span class="quiz-badge">⚡ Quick quiz</span>`;
      const topic = turn.topic ? `<span class="quiz-topic">${escapeHtml(turn.topic)}</span>` : "";
      let form = "";
      const isMC = turn.type === "multiple_choice" && Array.isArray(turn.options) && turn.options.length === 4;
      if (isMC) {
        // Multiple-choice options are always rendered so they stay visible as
        // a reference while the student reads the grading result. Only the
        // radio inputs + submit/skip buttons require an active quiz.
        const letters = ["A", "B", "C", "D"];
        if (isActive) {
          const radios = turn.options.map((opt, i) => {
            const letter = letters[i];
            return `<label class="quiz-mc-option">` +
              `<input type="radio" name="quiz-mc" value="${escapeAttr(letter)}" class="quiz-mc-radio" />` +
              `<span class="quiz-mc-letter">${letter}</span>` +
              `<span class="quiz-mc-text">${escapeHtml(opt)}</span>` +
              `</label>`;
          }).join("");
          form = `<div class="quiz-answer-form quiz-mc-form">` +
            `<div class="quiz-mc-options" role="radiogroup" aria-label="Answer choices">${radios}</div>` +
            `<div class="quiz-answer-actions">` +
            `<button class="btn" onclick="submitQuizAnswer()">Submit</button>` +
            `<button class="btn btn-ghost" onclick="dismissQuiz()">Skip</button>` +
            `</div></div>`;
        } else {
          // Read-only: show the options as plain text for reference.
          const opts = turn.options.map((opt, i) =>
            `<div class="quiz-mc-option quiz-mc-option--readonly">` +
            `<span class="quiz-mc-letter">${letters[i]}</span>` +
            `<span class="quiz-mc-text">${escapeHtml(opt)}</span>` +
            `</div>`
          ).join("");
          form = `<div class="quiz-mc-options quiz-mc-options--readonly">${opts}</div>`;
        }
      } else if (isActive) {
        // Open-ended text: textarea shown only while the quiz is active.
        form = `<div class="quiz-answer-form">` +
          `<textarea id="quiz-answer-input" class="quiz-answer-input" rows="3" ` +
          `placeholder="Type your answer…" aria-label="Quiz answer"></textarea>` +
          `<div class="quiz-answer-actions">` +
          `<button class="btn" onclick="submitQuizAnswer()">Submit answer</button>` +
          `<button class="btn btn-ghost" onclick="dismissQuiz()">Skip</button>` +
          `</div></div>`;
      }
      html += `<div class="prof-msg quiz-msg">` +
        `<span class="prof-avatar">${avatarHtml(currentPersona)}</span>` +
        `<div class="prof-bubble quiz-bubble">` +
        `${badge}${topic}` +
        `<p class="quiz-question">${escapeHtml(turn.question)}</p>` +
        `${form}${explainTimeHtml(turn.ts)}</div></div>`;

    } else if (turn.role === "quiz_result") {
      // The grading result, shown as a professor reply.
      const badge = _gradeBadge(turn.grade);
      const feedback = turn.feedback
        ? `<p class="quiz-feedback">${escapeHtml(turn.feedback)}</p>` : "";
      const ref = (turn.grade !== "correct" && turn.expected_answer)
        ? `<details class="quiz-ref"><summary>See reference answer</summary>` +
          `<p>${escapeHtml(turn.expected_answer)}</p></details>` : "";
      html += `<div class="prof-msg">` +
        `<span class="prof-avatar">${avatarHtml(currentPersona)}</span>` +
        `<div class="prof-bubble quiz-result-bubble">` +
        `${badge}${feedback}${ref}${explainTimeHtml(turn.ts)}</div></div>`;

    } else {
      html += `<div class="prof-msg"><span class="prof-avatar">${avatarHtml(turn.persona)}</span>` +
        `<div class="prof-bubble">${renderParagraphs(turn.content)}` +
        `${explainTimeHtml(turn.ts)}</div></div>`;
    }
  }
  if (pending) {
    // A transient animated typing-indicator bubble shown while the model is
    // generating. The title carries the status text ("Thinking…" / "Reading
    // your circuit…") for hover/screen-reader; the dots animate via CSS.
    // No circuit thumbnail here — the picture lives on the student's turn above.
    html += `<div class="prof-msg"><span class="prof-avatar">${avatarHtml()}</span>` +
      `<div class="prof-bubble"><div class="typing-indicator" title="${escapeAttr(pending)}">` +
      `<span></span><span></span><span></span></div></div></div>`;
  }
  out.innerHTML = html;
  out.scrollTop = out.scrollHeight; // keep the newest message in view
  // Re-focus the answer input when a text quiz just appeared.
  const qi = $("quiz-answer-input");
  if (qi) qi.focus();
}

// Wipe the conversation and start fresh.
function clearConversation() {
  conversation = [];
  lastExplainedSig = null;
  activeQuiz = null;
  renderConversation();
}

// Skip/dismiss a pending quiz without answering.
function dismissQuiz() {
  if (!activeQuiz) return;
  activeQuiz = null;
  _updateQuizBtn();
  renderConversation();
}

// ---- Learner-id helpers -----------------------------------------------------
// Ensure learnerId is set: read from localStorage, or mint a new one via POST
// /learner and persist it. Called once after loadConfig confirms memory is on.
async function ensureLearnerId() {
  const stored = localStorage.getItem("qcb_learner_id");
  if (stored) {
    // Verify the stored learner still exists — the DB could have been wiped or
    // the row deleted since the id was saved.  A 404 means we must mint fresh;
    // a network error means we keep the id optimistically and let the quiz/
    // interactions routes handle any failure gracefully.
    try {
      const check = await fetch(`/learner/${stored}`);
      if (check.ok) { learnerId = stored; return; }
      if (check.status === 404) {
        // Ghost id — clear it and fall through to mint a new one.
        localStorage.removeItem("qcb_learner_id");
      } else {
        // Server error (503 etc.) — keep the id, don't re-mint.
        learnerId = stored; return;
      }
    } catch (_) {
      // Network error — keep the id optimistically.
      learnerId = stored; return;
    }
  }
  try {
    const res = await fetch("/learner", { method: "POST" });
    if (!res.ok) return;                 // DB might be down; carry on without id
    const data = await res.json();
    learnerId = data.id || null;
    if (learnerId) localStorage.setItem("qcb_learner_id", learnerId);
  } catch (_) { /* best-effort — the tutor works without a learner id */ }
}

// ---- Onboarding and profile -------------------------------------------------

// After ensureLearnerId(), check if the learner has been onboarded.
// Fetches GET /learner/{id} to get the profile; shows the intake modal if
// onboarded_at is null and the student hasn't dismissed it this session.
async function checkOnboardingNeeded() {
  if (!learnerId) return;
  try {
    const res = await fetch('/learner/' + learnerId);
    if (!res.ok) return;
    learnerProfile = await res.json();
    _updateProfileBtn();
    if (!learnerProfile.onboarded_at) {
      // New learner. Show the intake modal unless they skipped it this session.
      if (!sessionStorage.getItem('qcb_skip_onboarding')) {
        setTimeout(showOnboardingModal, 350);
      }
    }
  } catch (_) { /* best-effort */ }
}

// Show the onboarding intake modal, populating the professor avatar.
function showOnboardingModal() {
  const modal = $('onboarding-modal');
  if (!modal) return;
  const av = $('onboarding-avatar');
  if (av) av.innerHTML = avatarHtml(currentPersona);
  modal.classList.remove('hidden');
  setTimeout(() => { const inp = $('onboarding-input'); if (inp) inp.focus(); }, 60);
}

// Dismiss the modal for this browser session without calling the API.
function skipOnboarding() {
  sessionStorage.setItem('qcb_skip_onboarding', '1');
  const modal = $('onboarding-modal');
  if (modal) modal.classList.add('hidden');
}

// Submit the intake answer to POST /learner/{id}/onboarding.
async function submitOnboarding() {
  const inp = $('onboarding-input');
  const answer = inp ? inp.value.trim() : '';
  if (!answer) { if (inp) inp.focus(); return; }
  if (!learnerId) return;
  const btn = $('onboarding-submit');
  const status = $('onboarding-status');
  if (btn) { btn.disabled = true; btn.textContent = 'Processing…'; }
  if (status) status.textContent = '';
  try {
    const res = await fetch('/learner/' + learnerId + '/onboarding', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer, provider: currentProvider, model: currentModel }),
    });
    if (res.ok) {
      learnerProfile = await res.json();
      _updateProfileBtn();
      const modal = $('onboarding-modal');
      if (modal) modal.classList.add('hidden');
    } else {
      if (status) status.textContent = "Couldn't save — try again later.";
    }
  } catch (_) {
    if (status) status.textContent = "Couldn't reach the server.";
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Tell the professor'; }
  }
}

// Update the profile button label with the learner's stored name (if any).
function _updateProfileBtn() {
  const btn = $('profile-btn');
  if (!btn) return;
  const name = learnerProfile && learnerProfile.display_name;
  btn.textContent = name ? ('\u{1F464} ' + name) : '\u{1F464} Profile';
}

// Toggle the collapsible profile edit panel.
function toggleProfilePanel() {
  const panel = $('profile-panel');
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  if (isHidden) {
    loadProfileIntoPanel();
    panel.classList.remove('hidden');
  } else {
    panel.classList.add('hidden');
  }
}

// Populate the profile form fields from the stored learnerProfile object.
function loadProfileIntoPanel() {
  if (!learnerProfile) return;
  const set = (id, val) => { const el = $(id); if (el) el.value = val || ''; };
  set('pf-name',       learnerProfile.display_name);
  set('pf-level',      learnerProfile.level);
  set('pf-background', learnerProfile.background);
  set('pf-interests',  learnerProfile.interests);
  set('pf-goals',      learnerProfile.goals);
  const saved = $('profile-saved');
  if (saved) saved.textContent = '';
}

// PUT updated profile fields to /learner/{id}/profile.
async function saveProfile() {
  if (!learnerId) return;
  const get = (id) => { const el = $(id); return el ? el.value.trim() : ''; };
  const body = {};
  const name = get('pf-name');       if (name)       body.display_name = name;
  const lvl  = get('pf-level');      if (lvl)        body.level        = lvl;
  const bg   = get('pf-background'); if (bg)         body.background   = bg;
  const int  = get('pf-interests');  if (int)        body.interests    = int;
  const goal = get('pf-goals');      if (goal)       body.goals        = goal;
  const saveBtn = $('profile-save-btn');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }
  try {
    const res = await fetch('/learner/' + learnerId + '/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      learnerProfile = await res.json();
      _updateProfileBtn();
      const saved = $('profile-saved');
      if (saved) {
        saved.textContent = 'Saved ✓';
        setTimeout(() => { if (saved) saved.textContent = ''; }, 2500);
      }
    }
  } catch (_) { /* best-effort */ }
  finally {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
  }
}

// ---- Quiz helpers -----------------------------------------------------------
// Build a plain-text context string from the last N assistant turns in the
// conversation — enough for the model to generate a relevant question.
function _recentContext(nTurns = 3) {
  const asst = conversation.filter((t) => t.role === "assistant").slice(-nTurns);
  return asst.map((t) => t.content).join("\n\n---\n\n");
}

// How many assistant turns have accumulated since the last quiz was answered
// (or the start of the conversation if none).
function _assistantTurnsSinceLastQuiz() {
  let count = 0;
  for (let i = conversation.length - 1; i >= 0; i--) {
    const t = conversation[i];
    if (t.role === "quiz_result") break;
    if (t.role === "assistant") count++;
  }
  return count;
}

// Returns true when it is time to auto-fire a quiz (enough assistant turns,
// memory wired, learner known, no quiz already active/pending, AI on).
function quizDueNow() {
  if (!memoryEnabled || !learnerId || activeQuiz || quizzing) return false;
  const count = _assistantTurnsSinceLastQuiz();
  return count > 0 && count % quizInterval === 0;
}

// Fetch one page of older interactions from the DB and prepend them to
// historicalTurns. Restores the scroll position so the view doesn't jump.
// Called on startup and each time the user scrolls to the top.
async function loadHistoricalMessages() {
  if (!memoryEnabled || !learnerId || historyLoading || !historyHasMore) return;
  historyLoading = true;
  renderConversation(); // shows "Loading history…" sentinel
  try {
    const params = new URLSearchParams({ limit: HISTORY_PAGE_SIZE });
    if (historyOldestId !== null) params.set("before_id", historyOldestId);
    const res = await fetch(`/learner/${learnerId}/interactions?${params}`);
    if (!res.ok) { historyHasMore = false; return; }
    const rows = await res.json();
    if (!rows.length) { historyHasMore = false; return; }

    // Map DB rows to the same shape used by `conversation`.
    const mapped = rows.map((r) => ({
      role: r.role,
      content: r.content,
      persona: r.persona || "professor",
      ts: r.created_at ? new Date(r.created_at).getTime() : null,
    }));

    // Capture scroll position before we change the DOM so we can restore it.
    const out = $("explain-out");
    const prevScrollHeight = out ? out.scrollHeight : 0;

    // Rows are oldest-first from the server. rows[0].id is the oldest.
    historyOldestId = rows[0].id;
    historicalTurns = [...mapped, ...historicalTurns];
    if (rows.length < HISTORY_PAGE_SIZE) historyHasMore = false;

    renderConversation();

    // Restore scroll position: after prepending new content above the fold,
    // adjust scrollTop by the height delta so nothing jumps.
    if (out && prevScrollHeight) {
      out.scrollTop += out.scrollHeight - prevScrollHeight;
    }
  } catch (_) {
    historyHasMore = false; // stop retrying if the endpoint is down
  } finally {
    historyLoading = false;
  }
}

// Attach a one-time scroll listener to #explain-out: when the user scrolls
// within 80px of the top, load the next (older) page of history.
function _attachHistoryScrollListener() {
  const out = $("explain-out");
  if (!out) return;
  out.addEventListener("scroll", function _onScroll() {
    if (out.scrollTop < 80 && historyHasMore && !historyLoading) {
      loadHistoricalMessages();
    }
  }, { passive: true });
}

// Post to /learner/{id}/quiz and, on success, push a quiz turn + re-render.
async function triggerQuiz() {
  if (!memoryEnabled || !learnerId || quizzing || activeQuiz) return;
  const context = _recentContext(3);
  // Empty context is fine — the backend generates a general quantum quiz.
  quizzing = true;
  _updateQuizBtn();
  try {
    const res = await fetch(`/learner/${learnerId}/quiz`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        context,
        provider: currentProvider,
        model: currentModel,
      }),
    });
    if (!res.ok) return;       // graceful: skip the quiz if the server errors
    const data = await res.json();
    activeQuiz = {
      quiz_id: data.quiz_id,
      question: data.question,
      topic: data.topic,
      type: data.type || "text",
      options: data.options || null,   // array of 4 strings for MC, null for text
    };
    conversation.push({ role: "quiz", ...activeQuiz, ts: Date.now() });
    renderConversation();
  } catch (_) { /* best-effort */ }
  finally {
    quizzing = false;
    _updateQuizBtn();
  }
}

// Submit the student's answer to an active quiz, grade it, push result turn.
async function submitQuizAnswer() {
  if (!activeQuiz || quizzing) return;
  // Collect the answer from either the MC radio group or the text textarea.
  let answer = "";
  if (activeQuiz.type === "multiple_choice") {
    const checked = document.querySelector("input.quiz-mc-radio:checked");
    answer = checked ? checked.value : "";
  } else {
    const input = $("quiz-answer-input");
    answer = input ? input.value.trim() : "";
  }
  if (!answer) return;
  quizzing = true;
  _updateQuizBtn();
  const { quiz_id } = activeQuiz;
  try {
    const res = await fetch(`/learner/${learnerId}/quiz/${quiz_id}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        learner_answer: answer,
        provider: currentProvider,
        model: currentModel,
      }),
    });
    // Push the student's answer as a user turn.
    conversation.push({ role: "user", content: answer, ts: Date.now() });
    activeQuiz = null;
    if (res.ok) {
      const data = await res.json();
      conversation.push({
        role: "quiz_result",
        grade: data.grade,
        score: data.score,
        feedback: data.feedback,
        expected_answer: data.expected_answer,
        ts: Date.now(),
      });
    } else {
      conversation.push({
        role: "quiz_result",
        grade: "error",
        feedback: "Grading failed — try again later.",
        ts: Date.now(),
      });
    }
    renderConversation();
  } catch (_) {
    activeQuiz = null;
    renderConversation();
  } finally {
    quizzing = false;
    _updateQuizBtn();
  }
}

// Keep the "Quiz me" button in sync with state.
function _updateQuizBtn() {
  const btn = $("quiz-btn");
  if (!btn) return;
  btn.disabled = !memoryEnabled || !learnerId || quizzing || explaining || !!activeQuiz;
  btn.textContent = quizzing ? "Quizzing…" : "Quiz me";
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
  _updateQuizBtn();

  // Snapshot the conversation history BEFORE adding the current turn — the
  // history is context for the model; the current question is passed separately.
  const history = conversation
    .filter((t) => t.role === "user" || t.role === "assistant")
    .map((t) => ({ role: t.role, content: t.content }));

  // Push the user turn to conversation immediately so it stays visible through
  // any incidental renderConversation() calls (e.g. history lazy-loading). For
  // the overview, the thumbnail starts as a "loading" sentinel and is swapped
  // for the real PNG just before the final re-render. Follow-up questions never
  // get a thumbnail — only the "Explain this circuit" overview shows the canvas.
  const ts = Date.now();
  const userTurn = {
    role: "user",
    content: question || OVERVIEW_PROMPT,
    circuit: question ? null : "loading",
    ts,
  };
  conversation.push(userTurn);
  renderConversation(question ? "Thinking…" : "Reading your circuit…");

  // Start PNG capture in parallel (overview only). Not awaited here so the
  // fetch begins immediately; the result is applied just before the final render.
  const snapshotPromise = question
    ? Promise.resolve(null)
    : circuitSnapshotUrl().catch(() => null);

  const payload = {
    num_qubits: state.numQubits,
    shots: state.shots,
    mode: "sim",
    gates: gatePayload(),
    history,                           // prior context, NOT including the current turn
    persona: currentPersona,           // which voice answers (server validates the key)
    provider: currentProvider,         // which AI provider runs it (server validates)
    model: currentModel,               // which model (server validates against provider)
  };
  if (question) payload.question = question;
  // Thread the learner id so the backend can look up the stored profile (which
  // the professor injects into the system prompt) and persist this turn for
  // long-term recall. Best-effort: explain still works if learnerId is null.
  if (learnerId) payload.learner_id = learnerId;

  try {
    const res = await fetch("/explain/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      userTurn.circuit = null; // clear loading sentinel if PNG hadn't landed yet
      renderConversation();
      $("explain-out").insertAdjacentHTML(
        "beforeend",
        `<div class="hint">${escapeHtml(err.detail || "The explainer is unavailable right now.")}</div>`,
      );
      return;
    }

    // ---- SSE stream reader ------------------------------------------------
    // Replace the typing-indicator row with a fresh streaming bubble. Using a
    // newly created element (rather than searching for the existing indicator)
    // is resilient to any renderConversation() calls that may have run between
    // the fetch start and now (e.g. the history lazy-loader wipes the DOM).
    const out = $("explain-out");
    const tyRow = out.querySelector(".typing-indicator");
    if (tyRow) tyRow.closest(".prof-msg").remove();

    let streamRow = document.createElement("div");
    streamRow.className = "prof-msg";
    streamRow.innerHTML =
      `<span class="prof-avatar">${avatarHtml(currentPersona)}</span>` +
      `<div class="prof-bubble is-streaming"></div>`;
    out.appendChild(streamRow);
    let streamBubble = streamRow.querySelector(".prof-bubble");
    out.scrollTop = out.scrollHeight;

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let fullText = "";
    let finalMeta = null;
    let streamError = null;

    outer: while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop(); // keep the last incomplete chunk in the buffer
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        let evt;
        try { evt = JSON.parse(part.slice(6)); } catch (_) { continue; }

        if (evt.error) {
          streamError = evt.error;
          break outer;
        }
        if (evt.delta) {
          fullText += evt.delta;
          // If renderConversation() was called mid-stream (e.g. by the history
          // loader), streamRow was removed from the DOM. Re-append it so the
          // streaming text stays visible.
          if (!out.contains(streamRow)) {
            streamRow = document.createElement("div");
            streamRow.className = "prof-msg";
            streamRow.innerHTML =
              `<span class="prof-avatar">${avatarHtml(currentPersona)}</span>` +
              `<div class="prof-bubble is-streaming"></div>`;
            out.appendChild(streamRow);
            streamBubble = streamRow.querySelector(".prof-bubble");
          }
          streamBubble.innerHTML = renderParagraphs(fullText);
          out.scrollTop = out.scrollHeight;
        }
        if (evt.done) {
          finalMeta = evt;
          break outer;
        }
      }
    }

    if (streamBubble) streamBubble.classList.remove("is-streaming");

    if (streamError) {
      userTurn.circuit = null;
      renderConversation();
      $("explain-out").insertAdjacentHTML(
        "beforeend",
        `<div class="hint">${escapeHtml(streamError || "The explainer is unavailable right now.")}</div>`,
      );
      return;
    }

    // Apply the captured circuit thumbnail before the final re-render so the PNG
    // appears in the student's bubble immediately. For questions, null (no thumbnail).
    userTurn.circuit = await snapshotPromise;

    // Commit the assistant response. The streaming row is replaced by the
    // renderConversation() call below which builds everything from scratch.
    conversation.push({
      role: "assistant",
      content: fullText.trim(),
      persona: (finalMeta && finalMeta.persona) || currentPersona,
      ts: Date.now(),
    });
    renderConversation();
    // Only the overview path dedupes against the circuit signature; a question is
    // always sent (the student may ask several things about the same circuit).
    if (!question) lastExplainedSig = circuitSig();
    // Auto-quiz after every N assistant turns (best-effort; never blocks the UI).
    if (quizDueNow()) triggerQuiz();
  } catch (err) {
    userTurn.circuit = null;
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
    _updateQuizBtn();
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
      aiEnabled = true;
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
    // Tutor-memory features (learner profiles, quizzes) need the DB layer.
    if (cfg.memory_enabled) {
      memoryEnabled = true;
      if (Number.isInteger(cfg.quiz_interval) && cfg.quiz_interval > 0) {
        quizInterval = cfg.quiz_interval;
      }
      // Mint or restore a learner id so quiz and profile calls have something to
      // attach to. Best-effort: the tutor still works if the DB is down.
      await ensureLearnerId();
      if (!learnerId) {
        // POST /learner returned 503 — the database is configured but not reachable.
        // Downgrade memory features to unavailable so the buttons stay hidden rather
        // than appearing enabled or appearing greyed-out with no explanation.
        memoryEnabled = false;
      } else {
        // Load the most recent page of past interactions so returning students
        // see their history immediately. Attach the scroll listener for more.
        await loadHistoricalMessages();
        _attachHistoryScrollListener();
        // Fetch the stored profile and, for new learners, show the intake modal.
        // Only when AI is also enabled — the onboarding endpoint requires it.
        if (cfg.ai_enabled) await checkOnboardingNeeded();
      }
    }
    // Visibility: driven by the config flag (driver + URL present) so the button
    // is always shown when memory is configured, even if the DB is temporarily
    // unreachable.  Enabledness is driven by the runtime memoryEnabled/learnerId
    // state — _updateQuizBtn() will disable it when the DB is actually down.
    const quizBtn = $("quiz-btn");
    if (quizBtn) {
      quizBtn.hidden = !cfg.memory_enabled;
      _updateQuizBtn();
    }
    const profileBtn = $("profile-btn");
    if (profileBtn) profileBtn.hidden = !cfg.memory_enabled;
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

// Collect the last n user/assistant turns (oldest first) as a plain
// {role, content} list for the handoff context payload. Quiz and quiz_result
// turns are stripped: the backend's _clean_history() rejects unknown roles.
function _recentConversationTurns(n = 10) {
  return conversation
    .filter((t) => t.role === "user" || t.role === "assistant")
    .slice(-n)
    .map((t) => ({ role: t.role, content: t.content }));
}

// Call /persona/handoff and inject the farewell + greeting turns into the
// conversation so the switch feels natural. Best-effort: if the request fails
// for any reason the persona has already switched; we just skip the messages.
// Only fires when AI is on and there are already assistant turns to hand off from.
async function performHandoff(fromKey, toKey) {
  if (!aiEnabled || fromKey === toKey) return;
  if (!conversation.some((t) => t.role === "assistant")) return;
  // Show a typing indicator immediately — the handoff makes two LLM calls
  // server-side, which can take 5–20 s. Any exit path clears it via renderConversation().
  renderConversation("Switching personas…");
  try {
    const res = await fetch("/persona/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        from_persona: fromKey,
        to_persona: toKey,
        provider: currentProvider,
        model: currentModel,
        history: _recentConversationTurns(10),
      }),
    });
    if (!res.ok) { renderConversation(); return; }  // clear indicator; persona already switched
    const data = await res.json();
    const now = Date.now();
    if (data.farewell) {
      conversation.push({ role: "assistant", persona: fromKey, content: data.farewell, ts: now });
    }
    if (data.greeting) {
      conversation.push({ role: "assistant", persona: toKey, content: data.greeting, ts: now });
    }
    renderConversation();
  } catch (_) {
    // Network hiccup: persona already switched, just no handoff messages.
    renderConversation();  // clear the pending indicator
  }
}

// Pick a persona from the menu: apply it and close, then (when AI is on and
// the conversation has content) trigger an in-character farewell + greeting.
async function choosePersona(key) {
  const prev = currentPersona;
  applyPersona(key);
  setPersonaMenuOpen(false);
  $("persona-trigger") && $("persona-trigger").focus();
  await performHandoff(prev, key);
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
