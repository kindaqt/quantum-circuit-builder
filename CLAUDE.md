# Quantum Circuit Playground — Project Rules

An interactive, browser-based quantum circuit builder. A **FastAPI + Qiskit** backend
(`backend/`) builds a circuit from a JSON spec and simulates it; a
**dependency-free vanilla-JS** frontend (`frontend/`) is served as static files at `/`.
Run it with `make run` or the preview server config **`quantum-playground`** (uvicorn,
port 8533).

## Architecture at a glance
- **Backend is split into three modules** (entry point stays `backend.main:app`):
  - `backend/core.py` — all domain logic: config, the gate whitelist + `validate()`,
    `build_circuit`, the quantum run paths, personas, and the AI-explainer dispatch.
    It validates a `CircuitSpec`, builds a `qiskit.QuantumCircuit`, and returns
    statevector / counts / Bloch results. Three run modes: `sim` (classical:
    exact statevector + Bloch, with the measurement histogram from a local Aer run
    — falling back to sampling the exact distribution if Aer is missing), `qsim`
    (local Aer measurement, counts only), `quantum` (IBM Runtime).
  - `backend/api.py` — the FastAPI `app` and the four routes (`/explain`, `/config`,
    `/simulate`, `/export`); a thin adapter that references everything as `core.<name>`
    (so tests can monkeypatch `core.*` and the routes pick it up).
  - `backend/main.py` — a thin shim: re-exports `api.app` (keeping `backend.main:app`)
    and mirrors `core`'s public names for back-compat. Put new logic in `core`, new
    endpoints in `api` — not here.
  - `backend/knowledge.py` — the explainer's curated RAG corpus + retrieval (no LLM,
    no embeddings, NumPy only). `GATE_NOTES`/`CONCEPT_NOTES` ground a circuit by
    structured gate lookup; `TOPIC_NOTES` (algorithms/concepts/hardware) ground a
    free-text question via an offline TF-IDF retriever (`retrieve_topics`) tuned and
    regression-tested against `EVAL_CASES`. `core._build_prompt` injects the merge via
    `combined_reference_block`. Corpus is hand-authored — keep it truthful; every
    whitelisted gate must have a note (a test enforces it).
- `frontend/app.js` — UI, drag-and-drop, palettes, results rendering. `ALGOS` and the
  dice are draggable **preset circuits**.
- `frontend/dice.js` — **generated** by `backend/gen_dice.py`. Never hand-edit it.
- `.env` — config + the IBM API token. Gitignored. `.env.sample` documents the vars.

## Security — non-negotiable
1. **The gate whitelist is the security boundary.** `build_circuit` dispatches with
   `getattr(qc, g.name)(*args)`; this is only safe because `validate()` rejects any
   name not in `ALLOWED`. Never build a circuit from user input without `validate()`
   first. Adding to `ALLOWED` = exposing a new method to attackers — justify each one.
2. **Never weaken the resource bounds.** `MAX_QUBITS`, `MAX_GATES`, `MAX_SHOTS` cap an
   exponential-cost simulator on a single-worker server. Every entry point must enforce
   them.
3. **Never commit secrets.** `.env` contains a real IBM token; keep it gitignored, never
   echo/log/return it, never `git add -f .env`, and `/config` must not leak it.
4. **`quantum` mode costs real money/queue time.** It stays gated behind
   `ENABLE_QUANTUM_HW` and only runs on explicit user action — never auto-fire it on edits.

## Quantum correctness
- **Qiskit is little-endian:** in basis strings/counts, qubit 0 is the *rightmost* bit.
  Most "wrong answer" bugs are endianness — always state which bit is which qubit.
- Verify circuits empirically with the venv, not by eyeballing. `backend/gen_dice.py`
  asserts each die is exactly uniform; trust its printed `maxerr`/`leak`, not the JS.
- Statevector and Bloch vectors are **simulator-only** — real hardware (and `qsim`)
  returns measurement counts only. Don't claim otherwise in code or UI.

## Frontend conventions
- **No build step, no frameworks, no npm.** It must run by serving the static files.
  Reject anything that adds a dependency or transpile step.
- **`update()` vs `render()`:** `update()` = edit the circuit *and* re-simulate;
  `render()` = layout only (used on resize so random measurement sampling doesn't change
  with window size). Don't conflate them.
- **Backend ↔ frontend contract:** the `/simulate` request/response shape and the
  gate-name mapping (`GATES[label].m` ↔ backend `ALLOWED` key) are a contract — change
  both sides together.
- After a refactor, remove the orphans (dead JS functions, unused CSS, stale DOM ids).

## Workflow
- After non-trivial edits, use the **code-reviewer** agent; verify user-facing changes in
  the running app with the **qa-engineer** agent (don't declare "done" from code-reading);
  consult **quantum-expert** for circuit/physics questions and **security-engineer**
  before shipping anything touching input handling, the whitelist, secrets, or the IBM path.
- Prefer the preview server over ad-hoc `uvicorn`; reload the page after edits and wait for
  readiness before driving it. Keep `/simulate` test loops small (single worker, ~30s eval cap).
- Don't run `git` history-rewriting or destructive commands, and **don't commit unless
  explicitly asked.** When regenerating dice, edit `backend/gen_dice.py` then re-run it.

## Performance — it must feel instant
- **Never reintroduce `transpile()` in the Aer/`qsim` path.** Whitelisted gates run
  natively; transpiling cost ~4 s vs ~25 ms for a bare `sim.run`. (Real-device `quantum`
  mode legitimately needs a pass manager — that's the one exception.)
- Statevector sim and per-qubit Bloch tracing are `O(2^n)`; don't add work that simulates
  beyond `MAX_QUBITS`.
- Single worker: don't fire many sequential `/simulate` calls from the client — debounce
  (live sim is already ~120 ms) or sample `lastDistribution` locally instead.
- Measure before/after when changing the hot path; don't optimize on a hunch.

## Accessibility & UX
- Keep interactive elements usable: visible focus, reasonable contrast, hover/active
  affordances, and tooltips on palette gates (not on placed gates).
- Don't break keyboard or pointer interactions when refactoring drag/render code.

## Errors & robustness
- Backend status codes are a contract: invalid input → **422**, disabled feature → **403**,
  missing dep/credential → **503**, each with a clear `detail`. Never leak internals or the
  IBM token in an error message.
- Frontend must degrade gracefully: if `/simulate` or `/config` fails, show a hint and keep
  working — never crash the UI. `/config` is best-effort.

## Keep docs and strings truthful
- A preset/algorithm's one-line `desc`, the README, and this file must match what the code
  actually does. Update them in the *same* change, not later.

## Checklists
- **Adding a gate:** (1) add to backend `ALLOWED` (name → arity, takes_param); (2) add to
  `GATES` in `app.js` (m, arity, param?, `desc`); (3) handle rendering in `placeGate`/helpers
  if multi-qubit; (4) confirm `validate()` and the JS payload agree, then have **quantum-expert**
  check the semantics.
- **Adding an algorithm:** add to `ALGOS` with a verified gate list and an accurate `desc`;
  verify the output distribution in Qiskit.
- **Adding/changing a die:** edit `backend/gen_dice.py` and re-run it to regenerate
  `frontend/dice.js` — never hand-edit the generated file. Confirm the printed `maxerr`/`leak`.

## Style
Keep it interactive and approachable — the audience is a curious learner with quantum
basics, not a production ops team. Explanations should build intuition (superposition,
interference, entanglement), and comments should explain *why*, staying truthful after edits.
