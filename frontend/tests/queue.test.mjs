// Tests for the run-queue bookkeeping (queue.js). We exercise the self-contained
// logic — timestamp formatting, the pending-run count, the history-trimming policy,
// and the config cap setter — which is the part with real branching. The rendering
// helpers (renderQueue/updateRunAvailability) touch the DOM and live in the page;
// setQueueMax calls them, so we replace them with no-ops for that one test.
import test from "node:test";
import assert from "node:assert/strict";
import { loadFrontend } from "./harness.mjs";

function load() {
  return loadFrontend(["queue.js"]);
}

test("queueTimestamp formats a millisecond epoch and is empty for a falsy value", () => {
  const { run } = load();
  assert.equal(run("queueTimestamp(0)"), "");
  assert.equal(run("queueTimestamp(null)"), "");
  // Seconds are timezone-invariant, so a fixed UTC instant's :56 always survives.
  const s = run("queueTimestamp(Date.UTC(2026, 0, 1, 12, 34, 56))");
  assert.match(s, /\d/);
  assert.match(s, /56/);
});

test("pendingRuns counts only the in-flight runs", () => {
  const { run } = load();
  run(`runQueue.push(
    { status: 'pending' }, { status: 'done' },
    { status: 'pending' }, { status: 'failed' }
  )`);
  assert.equal(run("pendingRuns()"), 2);
});

test("trimQueue keeps every pending run and caps finished ones at HISTORY_MAX", () => {
  const { run } = load();
  run(`
    runQueue.length = 0;
    for (let i = 0; i < 3; i++) runQueue.push({ status: 'pending', id: 'p' + i });
    for (let i = 0; i < HISTORY_MAX + 5; i++) runQueue.push({ status: 'done', id: 'd' + i });
  `);
  run("trimQueue()");
  assert.equal(run("runQueue.filter((r) => r.status === 'pending').length"), 3);
  assert.equal(
    run("runQueue.filter((r) => r.status !== 'pending').length"),
    run("HISTORY_MAX"),
  );
});

test("trimQueue keeps the newest finished runs and drops the oldest", () => {
  const { run } = load();
  // Stored newest-first; after trimming, the survivors are the first HISTORY_MAX.
  run(`
    runQueue.length = 0;
    for (let i = 0; i < HISTORY_MAX + 3; i++) runQueue.push({ status: 'done', id: i });
  `);
  run("trimQueue()");
  assert.equal(run("runQueue.length"), run("HISTORY_MAX"));
  assert.equal(run("runQueue[0].id"), 0); // newest kept
  assert.equal(run("runQueue[runQueue.length - 1].id"), run("HISTORY_MAX - 1"));
});

test("setQueueMax accepts a positive integer and rejects non-positive values", () => {
  const { run } = load();
  // setQueueMax re-renders; stub the DOM-touching helpers it calls.
  run("renderQueue = function () {}; updateRunAvailability = function () {};");
  const before = run("QUEUE_MAX");
  run("setQueueMax(0)"); // rejected — not > 0
  assert.equal(run("QUEUE_MAX"), before);
  run("setQueueMax(5)"); // accepted
  assert.equal(run("QUEUE_MAX"), 5);
  run("setQueueMax(-3)"); // rejected
  assert.equal(run("QUEUE_MAX"), 5);
  run("setQueueMax(2.9)"); // accepted, floored to 2
  assert.equal(run("QUEUE_MAX"), 2);
});
