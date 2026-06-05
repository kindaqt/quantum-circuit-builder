// Test harness for the dependency-free frontend modules.
//
// The frontend ships as plain <script> files that share one global scope in the
// browser — there is no build step, no module system, and no npm (a hard project
// rule). To unit-test the pure logic without a browser or any new dependency, we
// load the *real* source files into a Node `vm` context that stubs just enough of
// the DOM for them to evaluate, then evaluate assertions inside that context.
//
// Key fact this relies on: top-level `const`/`let`/`function` bindings created by
// one `runInContext` call remain visible to later calls in the same context — so
// files loaded in order see each other's globals exactly as the page's <script>
// tags would. That lets us load, e.g., catalog.js then state.js and have state.js
// reference GATES from catalog.js.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const FRONTEND_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");

// A minimal stand-in DOM element: tolerant of the handful of reads/writes the
// modules do at load time and in the helpers under test (textContent, style,
// classList, event/attr no-ops). It deliberately isn't a real DOM — just enough
// that evaluating the source never throws.
function fakeEl() {
  return {
    textContent: "", innerHTML: "", value: "", hidden: false, src: "", disabled: false,
    title: "",
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
  };
}

function fakeDocument() {
  return {
    getElementById() { return fakeEl(); },
    querySelector() { return fakeEl(); },
    querySelectorAll() { return []; },
    createElement() { return fakeEl(); },
    addEventListener() {},
    body: fakeEl(),
  };
}

// Load the given frontend files (in order) into a fresh vm context and return a
// `run(expr)` that evaluates a JS expression/statement inside it. `globals` lets a
// caller inject stubs (e.g. an `update` no-op) that the loaded modules expect from
// files we intentionally don't load. A fresh context per call keeps each test's
// mutable module state (state, runQueue, …) isolated.
export function loadFrontend(files, globals = {}) {
  const sandbox = {
    document: fakeDocument(),
    console,
    ...globals,
  };
  sandbox.window = sandbox; // some modules reference `window`; point it at itself
  const context = vm.createContext(sandbox);
  for (const f of files) {
    const code = readFileSync(join(FRONTEND_DIR, f), "utf8");
    vm.runInContext(code, context, { filename: f });
  }
  return {
    context,
    run(expr) {
      return vm.runInContext(expr, context);
    },
    // Evaluate `expr` and bring the result across the realm boundary as plain
    // data. Objects/arrays created inside the vm have a different Array/Object
    // prototype than the test realm, which trips assert.deepStrictEqual's
    // prototype check — round-tripping through a JSON string (a primitive that
    // crosses cleanly) rebuilds them with the test realm's prototypes.
    runJSON(expr) {
      return JSON.parse(vm.runInContext(`JSON.stringify(${expr})`, context));
    },
  };
}
