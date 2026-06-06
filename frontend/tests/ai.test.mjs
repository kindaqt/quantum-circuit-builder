// Tests for ai.js — the AI professor module.
//
// ai.js is server-driven: it calls /config on startup, then /learner (POST) to
// mint a learner, /learner/{id}/interactions for history, /learner/{id}/quiz to
// generate quizzes, etc.  All fetch calls are stubbed so the tests run offline.
//
// The module shares one global scope with state.js (it reads `state`, `$`, etc.),
// so we load state.js first and inject the external helpers ai.js calls but that
// live in other files (renderParagraphs, escapeHtml, circuitSnapshotUrl, …).

import test from "node:test";
import assert from "node:assert/strict";
import { loadFrontend } from "./harness.mjs";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a persistent fake DOM: getElementById always returns the SAME element
 * object for the same id, so property mutations are visible across calls. */
function fakePersistentDocument() {
  const cache = Object.create(null);
  function fakeEl() {
    return {
      textContent: "", innerHTML: "", value: "", hidden: false, src: "",
      disabled: false, title: "",
      style: { setProperty() {}, removeProperty() {} },
      classList: {
        add() {}, remove() {}, toggle() { return false; }, contains() { return false; },
      },
      addEventListener() {}, removeEventListener() {}, setAttribute() {},
      getAttribute() { return null; }, appendChild() {}, focus() {},
      setPointerCapture() {}, releasePointerCapture() {},
      querySelector() { return fakeEl(); }, querySelectorAll() { return []; },
      closest() { return null; },
      getBoundingClientRect() { return { top: 0, bottom: 0, left: 0, right: 0 }; },
      scrollTop: 0, scrollHeight: 0, scrollLeft: 0,
    };
  }
  return {
    getElementById(id) {
      if (!cache[id]) cache[id] = fakeEl();
      return cache[id];
    },
    querySelector() { return fakeEl(); },
    querySelectorAll() { return []; },
    createElement() { return fakeEl(); },
    addEventListener() {},
    body: fakeEl(),
    _cache: cache,
  };
}

/** Build a fetch stub.  `routes` maps URL prefix/pattern → { status, json }.
 * The special key "*" is the catch-all fallback.
 * For POST /learner the id comes from the response body, not the URL. */
function fakeFetch(routes) {
  const calls = [];
  const fn = async function(url, opts = {}) {
    calls.push({ url, method: opts.method || "GET" });
    // Find the longest matching key.
    const key = Object.keys(routes)
      .filter((k) => k === "*" || url === k || url.startsWith(k))
      .sort((a, b) => b.length - a.length)[0] || "*";
    const spec = routes[key] || { status: 503, json: {} };
    return {
      ok: spec.status >= 200 && spec.status < 300,
      status: spec.status,
      async json() { return spec.json; },
      body: {
        getReader() {
          let done = false;
          return {
            async read() {
              if (done) return { done: true, value: undefined };
              done = true;
              // Simulate a minimal SSE done event.
              const body = `data: ${JSON.stringify({ done: true, provider: "anthropic", model: "m", persona: "professor" })}\n\n`;
              return { done: false, value: new TextEncoder().encode(body) };
            },
          };
        },
      },
    };
  };
  fn.calls = calls;
  return fn;
}

/** Stub globals that ai.js calls but that live in other frontend files.
 *
 * loadConfig() calls several functions from queue.js and circuit.js before it
 * reaches the memory-feature block.  Any uncaught ReferenceError inside the
 * loadConfig try-block is swallowed by the catch-all, which means the quiz
 * button setup code is never reached.  Stub every cross-file call here so
 * the function runs end-to-end.
 */
const AI_STUBS = {
  escapeHtml: (s) => String(s),
  escapeAttr: (s) => String(s),
  renderParagraphs: () => "<p>text</p>",
  explainTimeHtml: () => "",
  circuitSnapshotUrl: async () => null,
  gatePayload: () => [],
  hasContent: () => false,
  circuitSig: () => "sig0",
  // queue.js
  setQueueMax() {},
  updateRunAvailability() {},
  QUEUE_MAX: 2,
  // circuit.js
  render() {},
  // ai.js helper UI
  populatePersonaMenu() {},
  populateProviderSelect() {},
  populateModelSelect() {},
  applyPersona() {},
  applyModel() {},
  activateTab() {},
  _updateProfileBtn() {},
  toggleProfilePanel() {},
  loadProfileIntoPanel() {},
  // misc
  playConfetti() {},
  update() {},
  // state.js
  state: { numQubits: 1, shots: 128, gates: [], initialStates: [0] },
};

function loadAI(fetchRoutes = {}) {
  const doc = fakePersistentDocument();
  const fetch = fakeFetch(fetchRoutes);
  const storage = { _store: Object.create(null),
    getItem(k) { return this._store[k] ?? null; },
    setItem(k, v) { this._store[k] = String(v); },
    removeItem(k) { delete this._store[k]; },
  };

  const { context, run, runJSON } = loadFrontend(["catalog.js", "state.js", "ai.js"], {
    ...AI_STUBS,
    document: doc,
    fetch,
    localStorage: storage,
    sessionStorage: { ...storage, _store: Object.create(null) },
    TextEncoder,  // needed by the fake SSE reader in explainCircuit tests
    TextDecoder,
  });

  // Expose helpers for inspecting button state by going through the same $ fn
  // the module uses, so mutations done inside the VM are visible from outside.
  function btn(id) { return doc.getElementById(id); }

  return { run, runJSON, context, fetch, btn };
}

// ---------------------------------------------------------------------------
// _updateQuizBtn — disabled-state logic (synchronous)
// ---------------------------------------------------------------------------

test("_updateQuizBtn: disabled when memoryEnabled is false", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = false; learnerId = 'x'; quizzing = false; explaining = false; activeQuiz = null;");
  run("_updateQuizBtn()");
  assert.equal(btn("quiz-btn").disabled, true);
});

test("_updateQuizBtn: disabled when learnerId is null", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = true; learnerId = null; quizzing = false; explaining = false; activeQuiz = null;");
  run("_updateQuizBtn()");
  assert.equal(btn("quiz-btn").disabled, true);
});

test("_updateQuizBtn: disabled while quizzing", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = true; learnerId = 'x'; quizzing = true; explaining = false; activeQuiz = null;");
  run("_updateQuizBtn()");
  assert.equal(btn("quiz-btn").disabled, true);
});

test("_updateQuizBtn: disabled while explaining (streaming in progress)", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = true; learnerId = 'x'; quizzing = false; explaining = true; activeQuiz = null;");
  run("_updateQuizBtn()");
  assert.equal(btn("quiz-btn").disabled, true);
});

test("_updateQuizBtn: disabled when activeQuiz is set", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = true; learnerId = 'x'; quizzing = false; explaining = false; activeQuiz = { quiz_id: 1 };");
  run("_updateQuizBtn()");
  assert.equal(btn("quiz-btn").disabled, true);
});

test("_updateQuizBtn: enabled when all conditions clear", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = true; learnerId = 'abc'; quizzing = false; explaining = false; activeQuiz = null;");
  run("_updateQuizBtn()");
  assert.equal(btn("quiz-btn").disabled, false);
});

test("_updateQuizBtn: button text is 'Quizzing…' while quizzing, 'Quiz me' otherwise", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = true; learnerId = 'x'; quizzing = true; explaining = false; activeQuiz = null; _updateQuizBtn()");
  assert.equal(btn("quiz-btn").textContent, "Quizzing…");
  run("quizzing = false; _updateQuizBtn()");
  assert.equal(btn("quiz-btn").textContent, "Quiz me");
});

// ---------------------------------------------------------------------------
// loadConfig — button visibility when DB is down
// ---------------------------------------------------------------------------

test("loadConfig: quiz button visible but disabled when POST /learner returns 503 (DB configured but down)", async () => {
  const { run, btn } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    "/learner": { status: 503, json: { detail: "DB unavailable" } },
  });
  await run("loadConfig()");
  // Visibility is driven by cfg.memory_enabled (the driver+URL are configured),
  // so the button must be shown even when the DB is temporarily unreachable.
  assert.equal(btn("quiz-btn").hidden, false,
    "quiz button must be visible when memory is configured, even if DB is down");
  // But it must be disabled because learnerId is null.
  assert.equal(btn("quiz-btn").disabled, true,
    "quiz button must be disabled when the DB is unreachable (no learnerId)");
});

test("loadConfig: quiz and profile buttons visible but disabled when DB is down", async () => {
  const { run, btn } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    "/learner": { status: 503, json: {} },
  });
  await run("loadConfig()");
  assert.equal(btn("quiz-btn").hidden, false);
  assert.equal(btn("quiz-btn").disabled, true);
  assert.equal(btn("profile-btn").hidden, false);
});

test("loadConfig: quiz button shown and enabled when DB works", async () => {
  const LEARNER_ID = "aaaabbbb-0000-0000-0000-ccccddddeeee";
  const { run, btn } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    "/learner": { status: 200, json: { id: LEARNER_ID } },
    [`/learner/${LEARNER_ID}/interactions`]: { status: 200, json: [] },
    [`/learner/${LEARNER_ID}`]: { status: 200, json: { id: LEARNER_ID, onboarded_at: "2024-01-01" } },
  });
  await run("loadConfig()");
  assert.equal(btn("quiz-btn").hidden, false, "button should be visible");
  assert.equal(btn("quiz-btn").disabled, false, "button should be enabled");
  assert.equal(run("learnerId"), LEARNER_ID, "learnerId should be set");
  assert.equal(run("memoryEnabled"), true);
});

test("loadConfig: memoryEnabled stays false when DB is unavailable", async () => {
  const { run } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    "/learner": { status: 503, json: {} },
  });
  await run("loadConfig()");
  assert.equal(run("memoryEnabled"), false);
  assert.equal(run("learnerId"), null);
});

test("loadConfig: quiz button hidden when memory_enabled is false in config", async () => {
  const { run, btn } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: false, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
  });
  await run("loadConfig()");
  assert.equal(btn("quiz-btn").hidden, true);
});

test("loadConfig: learnerId restored from localStorage skips POST /learner", async () => {
  const STORED_ID = "stored-learner-id-123";
  const { run, fetch } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    // Interactions and learner profile lookups expected; no POST /learner
    "/learner": { status: 200, json: { id: "should-not-be-called-as-POST" } },
    [`/learner/${STORED_ID}/interactions`]: { status: 200, json: [] },
    [`/learner/${STORED_ID}`]: { status: 200, json: { id: STORED_ID, onboarded_at: null } },
  });
  // Plant a stored id in localStorage before loadConfig runs.
  run(`localStorage.setItem('qcb_learner_id', '${STORED_ID}')`);
  await run("loadConfig()");
  assert.equal(run("learnerId"), STORED_ID);
  // POST /learner should NOT have been called since localStorage had a valid id.
  const postLearner = fetch.calls.filter((c) => c.url === "/learner" && c.method === "POST");
  assert.equal(postLearner.length, 0, "must not POST /learner when localStorage has a valid id");
});

test("loadConfig: stale learnerId (404) clears localStorage and mints a new learner", async () => {
  const STALE_ID = "stale-id-from-wiped-db";
  const NEW_ID = "fresh-minted-learner-id";
  const { run, fetch } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    // GET /learner/{stale} returns 404 → should re-mint
    [`/learner/${STALE_ID}`]: { status: 404, json: { detail: "not found" } },
    // POST /learner mints a fresh one
    "/learner": { status: 200, json: { id: NEW_ID } },
    [`/learner/${NEW_ID}/interactions`]: { status: 200, json: [] },
    [`/learner/${NEW_ID}`]: { status: 200, json: { id: NEW_ID, onboarded_at: null } },
  });
  run(`localStorage.setItem('qcb_learner_id', '${STALE_ID}')`);
  await run("loadConfig()");
  assert.equal(run("learnerId"), NEW_ID, "should have minted a new learner after 404");
  const postLearner = fetch.calls.filter((c) => c.url === "/learner" && c.method === "POST");
  assert.equal(postLearner.length, 1, "must POST /learner once when stored id is stale");
  assert.equal(run(`localStorage.getItem('qcb_learner_id')`), NEW_ID, "localStorage updated to new id");
});

// ---------------------------------------------------------------------------
// triggerQuiz — async flow
// ---------------------------------------------------------------------------

test("triggerQuiz: button re-enabled after a failed quiz request (503)", async () => {
  const LEARNER_ID = "quiz-test-learner-id";
  const { run, btn } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    "/learner": { status: 200, json: { id: LEARNER_ID } },
    [`/learner/${LEARNER_ID}/interactions`]: { status: 200, json: [] },
    [`/learner/${LEARNER_ID}`]: { status: 200, json: { id: LEARNER_ID, onboarded_at: "2024-01-01" } },
    [`/learner/${LEARNER_ID}/quiz`]: { status: 503, json: { detail: "DB down" } },
  });
  await run("loadConfig()");
  // Manually trigger quiz (bypass quizDueNow guard).
  await run("triggerQuiz()");
  assert.equal(btn("quiz-btn").disabled, false,
    "button must be re-enabled after a failed quiz request");
  assert.equal(run("quizzing"), false);
  assert.equal(run("activeQuiz"), null);
});

test("triggerQuiz: activeQuiz is set and button stays disabled on success", async () => {
  const LEARNER_ID = "quiz-success-learner";
  const { run, btn } = loadAI({
    "/config": { status: 200, json: {
      memory_enabled: true, ai_enabled: false, ai_providers: [], personas: [],
      quiz_interval: 3, embeddings_enabled: false, queue_max: 2,
    }},
    "/learner": { status: 200, json: { id: LEARNER_ID } },
    [`/learner/${LEARNER_ID}/interactions`]: { status: 200, json: [] },
    [`/learner/${LEARNER_ID}`]: { status: 200, json: { id: LEARNER_ID, onboarded_at: "2024-01-01" } },
    [`/learner/${LEARNER_ID}/quiz`]: {
      status: 200,
      json: { quiz_id: 99, question: "What is superposition?", topic: "Superposition", type: "text" },
    },
  });
  await run("loadConfig()");
  await run("triggerQuiz()");
  assert.equal(run("quizzing"), false);
  assert.notEqual(run("activeQuiz"), null, "activeQuiz should be set after a successful quiz");
  assert.equal(btn("quiz-btn").disabled, true,
    "button must stay disabled while an active quiz awaits an answer");
});

test("triggerQuiz: does nothing when memoryEnabled is false", async () => {
  const { run, fetch } = loadAI({ "*": { status: 200, json: {} } });
  run("memoryEnabled = false; learnerId = 'x';");
  await run("triggerQuiz()");
  const quizCalls = fetch.calls.filter((c) => c.url.includes("/quiz"));
  assert.equal(quizCalls.length, 0);
});

test("triggerQuiz: does nothing when learnerId is null", async () => {
  const { run, fetch } = loadAI({ "*": { status: 200, json: {} } });
  run("memoryEnabled = true; learnerId = null;");
  await run("triggerQuiz()");
  const quizCalls = fetch.calls.filter((c) => c.url.includes("/quiz"));
  assert.equal(quizCalls.length, 0);
});

test("dismissQuiz: re-enables quiz button", () => {
  const { run, btn } = loadAI();
  run("memoryEnabled = true; learnerId = 'x'; activeQuiz = { quiz_id: 7 }; _updateQuizBtn()");
  assert.equal(btn("quiz-btn").disabled, true, "button disabled while quiz active");
  run("dismissQuiz()");
  assert.equal(btn("quiz-btn").disabled, false, "button re-enabled after dismiss");
  assert.equal(run("activeQuiz"), null);
});
