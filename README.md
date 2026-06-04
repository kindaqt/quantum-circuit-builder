# Quantum Circuit Playground

An interactive, browser-based quantum circuit builder. Drag gates (or whole
algorithms) onto a circuit and watch the measurement histogram, statevector, and
Bloch spheres update live. A FastAPI + [Qiskit](https://www.ibm.com/quantum/qiskit)
backend does the simulation; a dependency-free vanilla-JS frontend handles the UI.

## Features

- **Drag-and-drop gates** — single-qubit (H, X, Y, Z, S/Sdg, T/Tdg), rotations
  (RX, RY, RZ with adjustable angle), and multi-qubit gates (CX, CZ, SWAP, CP,
  Toffoli/CCX, Fredkin/CSWAP).
- **Drag-and-drop algorithms** — Bell, GHZ, Grover, Deutsch–Jozsa,
  Bernstein–Vazirani, QFT / inverse QFT, phase estimation, and Shor (N=15, a=7).
- **Quantum D&D dice** — d4, d6, d8, d10, d12, d20, d100. Each is a draggable
  preset circuit (like the algorithms): drop one onto the canvas to load its
  exact uniform-superposition prep gates, then press **Roll** to sample a face.
  (Circuits are generated and verified by `backend/gen_dice.py` into
  `frontend/dice.js`.)
- **Move placed gates** — drag a gate already on the circuit to a new column or
  qubit; the rest of the circuit reflows automatically.
- **Live results** — measurement histogram, full statevector table, and a Bloch
  sphere per qubit, recomputed on every edit.
- **Run on real quantum hardware** *(optional)* — set `QCB_ENABLE_QUANTUM_HW=true`
  to reveal a **Run on: Classical simulator / Quantum hardware** toggle. In
  quantum mode you press **Run** to submit the circuit through Qiskit Runtime to
  an IBM Quantum device (the run returns measurement counts only — statevector
  and Bloch tabs are unavailable, since they can't be observed on hardware). The
  default backend is a local Aer simulator, so the path works without
  credentials; point it at a real device with the `QCB_IBM_*` variables below.

## Requirements

- Python 3.10+
- `make` (optional but recommended)

## Getting started

```bash
# 1. clone, then from the project root:
cp .env.sample .env        # optional — defaults work out of the box

# 2. install dependencies into a local virtualenv
make install

# 3. run the app
make run                   # serves http://127.0.0.1:8533
```

Open <http://127.0.0.1:8533> in your browser. Use `make dev` for auto-reload
while editing the backend.

### Without make

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8533
```

## Configuration

Copy `.env.sample` to `.env` to override any of these (defaults shown):

| Variable          | Default     | Purpose                                   |
| ----------------- | ----------- | ----------------------------------------- |
| `HOST`            | `127.0.0.1` | Server bind address (Makefile / uvicorn). |
| `PORT`            | `8533`      | Server port.                              |
| `QCB_MAX_QUBITS`  | `12`        | Max qubits (statevector sim is exp. in n).|
| `QCB_MAX_GATES`   | `2000`      | Max gates per circuit.                    |
| `QCB_MAX_SHOTS`   | `100000`    | Max measurement shots.                    |
| `QCB_ENABLE_QUANTUM_HW` | `false` | Show the classical/quantum run toggle. |
| `QCB_IBM_BACKEND` | `aer`       | `aer` (local), `least_busy`, or a device name. |
| `QCB_IBM_CHANNEL` | `ibm_quantum_platform` | Qiskit Runtime channel. |
| `QCB_IBM_TOKEN`   | *(empty)*   | IBM Quantum API key (real devices only).  |
| `QCB_IBM_INSTANCE`| *(empty)*   | IBM Quantum instance/CRN (real devices).  |

The `QCB_MAX_*` limits are enforced by the backend to bound resource use —
statevector simulation grows exponentially with the qubit count.

### Running on real IBM Quantum hardware

1. Create a free account at <https://quantum.cloud.ibm.com> and copy your **API
   key** and **instance** (CRN). IBM's free Open Plan includes ~10 minutes of
   QPU time per month.
2. In `.env`, set `QCB_ENABLE_QUANTUM_HW=true`, paste `QCB_IBM_TOKEN` and
   `QCB_IBM_INSTANCE`, and set `QCB_IBM_BACKEND=least_busy` (or a device name).
3. Restart the server, switch the toggle to **Quantum hardware**, and press
   **Run**. Jobs queue, so a run can take a while. Keep `QCB_IBM_BACKEND=aer` to
   exercise the same path locally without spending QPU time.

> Note: real hardware (and the Aer path) returns **measurement counts only** —
> there is no readable statevector or Bloch vector. Credentials live in `.env`,
> which is gitignored; never commit them.

## Project layout

```
backend/main.py     FastAPI app: validates a circuit spec and simulates it.
backend/gen_dice.py Build-time tool: regenerates frontend/dice.js (needs the venv).
frontend/           Static UI (index.html, app.js, style.css) served at /.
frontend/dice.js    Generated uniform-superposition circuits for the D&D dice.
requirements.txt    Python dependencies.
Makefile            install / run / dev / clean targets.
```

## Make targets

| Target         | Description                                  |
| -------------- | -------------------------------------------- |
| `make install` | Create `.venv` and install dependencies.     |
| `make run`     | Start the server.                            |
| `make dev`     | Start with auto-reload.                      |
| `make clean`   | Remove `.venv` and Python caches.            |
