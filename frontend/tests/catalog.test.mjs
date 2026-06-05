// Tests for the gate catalog + algorithm presets (catalog.js) — pure data, so the
// harness needs no DOM stubs. The gate-name mapping (GATES[label].m) is a contract
// with the backend's ALLOWED whitelist, and each preset's gate list must reference
// real gates with the right arity and in-range qubits; these tests guard both.
import test from "node:test";
import assert from "node:assert/strict";
import { loadFrontend } from "./harness.mjs";

function load() {
  return loadFrontend(["catalog.js"]);
}

test("every gate has a backend method name, a valid arity, and a description", () => {
  const { runJSON } = load();
  const gates = runJSON("GATES");
  for (const [label, g] of Object.entries(gates)) {
    assert.ok(g.m, `${label} is missing its backend method name (.m)`);
    assert.ok(g.arity >= 1 && g.arity <= 3, `${label} arity ${g.arity} out of range`);
    assert.ok(g.desc, `${label} is missing a description`);
  }
});

test("only the rotation/phase gates are flagged as taking a parameter", () => {
  const { runJSON } = load();
  const params = runJSON("Object.keys(GATES).filter((k) => GATES[k].param)");
  assert.deepEqual(new Set(params), new Set(["RX", "RY", "RZ", "CP"]));
});

test("every preset references real gates with correct arity and in-range qubits", () => {
  const { runJSON } = load();
  // Validate inside the context so we compare against the real GATES/ALGOS data.
  const problems = runJSON(`(() => {
    const out = [];
    for (const [name, algo] of Object.entries(ALGOS)) {
      if (!(algo.n >= 1)) out.push(name + ": bad qubit count");
      for (const g of algo.gates) {
        const spec = GATES[g.label];
        if (!spec) { out.push(name + ": unknown gate " + g.label); continue; }
        if (g.qubits.length !== spec.arity) out.push(name + ": " + g.label + " wrong arity");
        if (new Set(g.qubits).size !== g.qubits.length) out.push(name + ": " + g.label + " repeats a qubit");
        if (g.qubits.some((q) => q < 0 || q >= algo.n)) out.push(name + ": " + g.label + " out-of-range qubit");
      }
    }
    return out;
  })()`);
  assert.deepEqual(problems, []);
});

test("the Bell preset is H then CX over two qubits", () => {
  const { run, runJSON } = load();
  assert.equal(run("ALGOS.Bell.n"), 2);
  assert.deepEqual(runJSON("ALGOS.Bell.gates.map((g) => g.label)"), ["H", "CX"]);
});

test("Shor's preset is assembled by shorGates() and stays within its 8 qubits", () => {
  const { run } = load();
  assert.equal(run("ALGOS['Shor (N=15, a=7)'].n"), 8);
  assert.equal(run("ALGOS['Shor (N=15, a=7)'].gates.length > 0"), true);
  const maxQ = run("Math.max(...ALGOS['Shor (N=15, a=7)'].gates.flatMap((g) => g.qubits))");
  assert.ok(maxQ < 8, "Shor preset must not touch a qubit beyond its register");
});

test("an unknown preset name is simply absent (sad path)", () => {
  const { run } = load();
  assert.equal(run("ALGOS['Not A Real Algorithm'] === undefined"), true);
});
