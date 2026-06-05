// Tests for the circuit state + mutations (state.js). state.js reads a few DOM
// refs at load time and calls update() (defined in circuit.js) on every mutation,
// so we stub update() and let the harness's fake DOM satisfy the lookups. catalog.js
// is loaded first because state.js reads GATES/ALGOS/P from it.
import test from "node:test";
import assert from "node:assert/strict";
import { loadFrontend } from "./harness.mjs";

function load() {
  return loadFrontend(["catalog.js", "state.js"], { update() {} });
}

test("pickQubits chooses distinct in-range qubits, wrapping around the register", () => {
  const { run, runJSON } = load();
  run("state.numQubits = 3");
  assert.deepEqual(runJSON("pickQubits(2, 2)"), [2, 0]); // wraps past the top
  assert.deepEqual(runJSON("pickQubits(0, 3)"), [0, 1, 2]);
});

test("pickQubits returns null when the arity exceeds the qubit count (sad path)", () => {
  const { run } = load();
  run("state.numQubits = 2");
  assert.equal(run("pickQubits(0, 3)"), null);
});

test("hasContent is false on a fresh circuit, true after a gate or an init flip", () => {
  const { run } = load();
  run("state.numQubits = 2; state.gates = []; state.initialStates = [0, 0]");
  assert.equal(run("hasContent()"), false);
  run("state.initialStates = [0, 1]"); // a |1>-initialised qubit counts as content
  assert.equal(run("hasContent()"), true);
});

test("gatePayload prepends an X prep gate for each |1>-initialised qubit", () => {
  const { run, runJSON } = load();
  run("state.numQubits = 2; state.gates = []; state.initialStates = [1, 0]");
  const payload = runJSON("gatePayload()");
  assert.deepEqual(payload, [{ name: "x", qubits: [0], param: null }]);
});

test("gatePayload maps placed gates to their backend method names", () => {
  const { run, runJSON } = load();
  run("state.numQubits = 2; state.initialStates = [0, 0]");
  run("state.gates = [{ id: 1, label: 'H', qubits: [0], param: null }]");
  const payload = runJSON("gatePayload()");
  assert.deepEqual(payload, [{ name: "h", qubits: [0], param: null }]);
});

test("addGate appends a single-qubit gate with a fresh id", () => {
  const { run, runJSON } = load();
  run("state.numQubits = 2; state.gates = []");
  run("addGate('H', 0)");
  assert.equal(run("state.gates.length"), 1);
  assert.equal(run("state.gates[0].label"), "H");
  assert.deepEqual(runJSON("state.gates[0].qubits"), [0]);
});

test("addGate ignores an unknown gate label (sad path)", () => {
  const { run } = load();
  run("state.numQubits = 2; state.gates = []");
  run("addGate('NOPE', 0)");
  assert.equal(run("state.gates.length"), 0);
});

test("setQubits clamps to [1, MAX_QUBITS] and drops now-out-of-range gates", () => {
  const { run } = load();
  run("state.numQubits = 3; state.initialStates = [0, 0, 0];" +
      "state.gates = [{ id: 1, label: 'CX', qubits: [1, 2], param: null }]");
  run("setQubits(2)");
  assert.equal(run("state.numQubits"), 2);
  assert.equal(run("state.gates.length"), 0); // the CX touched q2, which is gone
  run("setQubits(9999)");
  assert.equal(run("state.numQubits"), run("MAX_QUBITS"));
  run("setQubits(-5)");
  assert.equal(run("state.numQubits"), 1);
});

test("applyAlgorithm loads a preset's gates and grows the register to fit", () => {
  const { run, runJSON } = load();
  run("state.numQubits = 1; state.gates = []; state.initialStates = [0]");
  run("applyAlgorithm('Bell')");
  assert.ok(run("state.numQubits") >= 2);
  assert.deepEqual(runJSON("state.gates.map((g) => g.label)"), ["H", "CX"]);
});

test("applyAlgorithm is a no-op for an unknown preset name (sad path)", () => {
  const { run } = load();
  run("state.numQubits = 2; state.gates = []");
  run("applyAlgorithm('Definitely Not A Preset')");
  assert.equal(run("state.gates.length"), 0);
});
