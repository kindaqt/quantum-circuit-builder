# Quantum Circuit Playground

An interactive, browser-based quantum circuit builder. Drag gates (or whole
algorithms) onto a circuit and watch the measurement histogram, statevector, and
Bloch spheres update live. A FastAPI + [Qiskit](https://www.ibm.com/quantum/qiskit)
backend does the simulation; a dependency-free vanilla-JS frontend handles the UI.

![The Quantum Circuit Playground: a 3-qubit GHZ circuit (H on q0, then a CX chain)
with the gate, algorithm, and dice palettes above it. Below, the Professor panel
shows the Provider (Claude) and Model pickers and an open Persona menu whose
entries — Ada Lovelace, Albert Einstein, Batman, Beaker, Bob Ross — each carry a
custom caricature avatar.](docs/screenshot.png)

## Features

- **Build controls** — set the number of **Qubits** and measurement **Shots**
  from the toolbar, and watch a live **qubits · depth · gates** readout update as
  you build. **Undo** steps back through your edits and **Clear all** empties the
  canvas. The **Gates**, **Algorithms**, and **Dice** palettes each collapse to
  their label (click the label) so you can reclaim vertical space.
- **Drag-and-drop gates** — single-qubit (H, X, Y, Z, S/Sdg, T/Tdg), rotations
  (RX, RY, RZ with adjustable angle), and multi-qubit gates (CX, CZ, SWAP, CP,
  Toffoli/CCX, Fredkin/CSWAP). Hover a gate (or algorithm/die) in the palette for
  a second to see a tooltip explaining what it is and what it does.
- **Flip the starting state** — click a qubit's `|0⟩` label to start it in `|1⟩`
  instead (equivalent to an X at the front of the wire). The qubit labels and the
  **+ qubit** / **− qubit** buttons stay pinned to the left as the circuit scrolls.
- **Add or remove qubit lines** — the **+ qubit** button adds a wire (up to the
  configured maximum) and **− qubit** drops the last one, along with any gates that
  were sitting on it.
- **Drag-and-drop algorithms** — Bell, GHZ, Grover, Deutsch–Jozsa,
  Bernstein–Vazirani, QFT / inverse QFT, phase estimation, Shor (N=15, a=7),
  teleportation, superdense coding, Simon (s=11), and the swap test. (The
  teleportation and superdense presets use deferred-measurement corrections,
  since the simulator has no mid-circuit measurement.)
- **Quantum D&D dice** — d2 (coin), d4, d6, d8, d10, d12, d20, d100. Each die is
  dual-purpose: **click** it to roll — the die's circuit runs and the result
  flashes large in the centre of the screen — or **drag** it onto the canvas to
  load its exact uniform-superposition prep gates and inspect the full (flat)
  measurement histogram. Use the **Dice** selector to roll several at once (1–10);
  the result modal then shows each die's value prominently plus the **Total**. For
  a single die the modal also shows the **statevector behind the roll** — the
  uniform superposition the die is prepared in. The last five rolls are listed
  inside that modal, newest first. Every face is
  equally likely — and rolling a natural 20 on the d20 sets off a burst of
  fireworks. A roll uses the current **Run on** mode: the classical
  simulator by default, or real quantum hardware when that mode is selected — on
  hardware the result modal spins through random faces until the job returns.
  (Circuits are generated and verified by `backend/gen_dice.py` into
  `frontend/dice.js`.)
- **Move placed gates** — drag a gate already on the circuit to a new column or
  qubit; the rest of the circuit reflows automatically.
- **Live results** — measurement histogram, full statevector table, and a Bloch
  sphere per qubit, recomputed on every edit. The histogram toggles between raw
  **counts** and **probabilities**, sorts in basis order or by value (tallest
  first), and downloads as a **PNG** image or **CSV** of the outcomes. Drag the
  divider between the circuit and the results to retrade vertical space between
  the two panes.
- **Run & queue** — results update automatically as you edit, but pressing
  **Run** also logs a snapshot of the current circuit and its result to the
  **Queue** tab. Each run is a card showing the submitted circuit (click the
  picture to enlarge; it scrolls horizontally when the circuit is wide), a
  timestamp, and the measured histogram. Classical runs are recorded instantly;
  quantum-hardware runs (below) appear as **Pending** first and fill in when the
  device returns. The most-recent finished runs are kept, newest first.
- **Export your circuit** — press **Export** to open a modal with the current
  circuit as runnable **Qiskit** Python and as **OpenQASM 3**, each with a copy
  button. Both forms measure every qubit, so they reproduce the histogram you see
  in the app. The modal also has a **Download PNG** button that saves the circuit
  diagram as an image.
- **Run on real quantum hardware** *(optional)* — set `QCB_ENABLE_QUANTUM_HW=true`
  to reveal a **Run on: Classical simulator / Quantum hardware** toggle. The
  cloud provider is configurable via `QCB_QUANTUM_PROVIDER` — **IBM Quantum**
  (Qiskit Runtime) or **AWS Braket**. In quantum mode you press **Run** to submit
  the circuit to the chosen provider. A hardware run returns measurement counts
  only, so the Statevector and Bloch tabs are filled from a local simulation of
  the same circuit and clearly labeled as simulated (amplitudes and Bloch vectors
  can't be observed on hardware). Because real-device jobs queue for minutes,
  they share the **Queue** tab described above: a hardware run appears as
  **Pending** with a picture of the circuit you submitted, then fills in the
  measured histogram when the device returns. Up to `QCB_QUEUE_MAX` runs
  (default 2) can be in flight at once; while the queue is full the **Run**
  button is disabled with a tooltip explaining why. Each provider has a
  no-credentials local
  simulator default (Aer for IBM, `LocalSimulator` for Braket), so the path works
  out of the box; point it at a real device with the `QCB_IBM_*` / `QCB_BRAKET_*`
  variables below.
- **Ask the Professor** *(optional)* — set `QCB_ENABLE_AI=true` to reveal a
  **Professor** tab: a friendly instructor that explains, in plain language, what
  your circuit does and why it produces its distribution, answers your specific
  questions, and remembers the conversation for follow-ups — each exchange keeps a
  thumbnail of the circuit it was about (click to enlarge) and a timestamp.
  Answers render inline **Markdown** emphasis and **LaTeX bra-ket math** (kets,
  fractions, tensor products, roots) with a tiny built-in renderer — no MathJax,
  no extra requests. **Explain this circuit** works even on an empty canvas (it
  describes the `|0…0⟩` ground state). Pick the voice from a
  **Persona** dropdown stocked with 73 characters — each with its own **custom SVG
  avatar** drawn as a recognizable caricature of that person or character, shown
  both on the trigger and in the menu, plus a **hover tooltip** that says who they
  are. The cast spans real scientists (Feynman, Einstein, Marie Curie, Newton,
  Darwin, Bohr, Faraday, Tesla, Ada Lovelace, Carl Sagan, Neil deGrasse Tyson),
  superheroes (Tony Stark, Batman, Captain Marvel), and sci-fi characters from Star
  Trek, Star Wars, Stargate, the Muppets, Firefly, and more (Spock, Data, Yoda,
  Obi-Wan, Darth Vader, Jar Jar, Samantha Carter, Kermit, Beaker, Kaylee, plus a
  certain superposed cat). The Professor is pinned to the top; the rest are listed
  alphabetically. Each
  persona is just a *voice* — the physics stays correct and grounded in your real
  circuit. Runs on **Claude**, **Gemini**, **ChatGPT** (each needs a key) or a
  **local Llama** model via Ollama (no key) — and if you enable more than one,
  **Provider** and **Model** dropdowns let you switch live.
- **Graceful errors** — failures that don't have their own place in the UI (the
  simulator rejecting a circuit, a network call falling over) surface in a clear
  error dialog instead of failing silently, and recoverable problems (a missing
  `/config`, a one-off `/simulate` hiccup) leave the rest of the app working.

## Requirements

- Python 3.10+
- `make` (optional but recommended)
- Node 18+ (optional — only to run the frontend test suite; the app itself ships
  no JS toolchain)

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
| `QCB_MAX_QUBITS`  | `16`        | Max qubits (statevector sim is exp. in n).|
| `QCB_MAX_GATES`   | `2000`      | Max gates per circuit.                    |
| `QCB_MAX_SHOTS`   | `100000`    | Max measurement shots.                    |
| `QCB_ENABLE_QUANTUM_HW` | `false` | Show the classical/quantum run toggle. |
| `QCB_QUEUE_MAX`   | `2`         | Max concurrent quantum-hardware runs before Run is disabled. |
| `QCB_QUANTUM_PROVIDER` | `ibm`  | Quantum-run provider: `ibm` or `braket`. |
| `QCB_IBM_BACKEND` | `aer`       | (ibm) `aer` (local), `least_busy`, or a device name. |
| `QCB_IBM_CHANNEL` | `ibm_quantum_platform` | (ibm) Qiskit Runtime channel. |
| `QCB_IBM_TOKEN`   | *(empty)*   | (ibm) IBM Quantum API key (real devices only). |
| `QCB_IBM_INSTANCE`| *(empty)*   | (ibm) IBM Quantum instance/CRN (real devices). |
| `QCB_BRAKET_DEVICE` | `local`   | (braket) `local` simulator, or an AWS device ARN. |
| `QCB_ENABLE_AI`   | `false`     | Show the **Professor** tab.               |
| `QCB_AI_PROVIDER` | `anthropic` | Single-provider mode: `anthropic`, `gemini`, `openai`, or `llama`. |
| `QCB_AI_MODEL`    | `claude-opus-4-7` | Model used for explanations.        |
| `QCB_AI_API_KEY`  | *(empty)*   | API key for the chosen provider (kept out of `/config`). |
| `QCB_PROVIDER_<NAME>` | `false` | Multi-provider mode: toggle `ANTHROPIC`/`GEMINI`/`OPENAI`/`LLAMA` on; each then reads `QCB_<NAME>_API_KEY`/`_MODEL`/`_MODELS`. |
| `QCB_AI_LLAMA_HOST` | `http://localhost:11434` | Ollama server (llama provider). |

The `QCB_MAX_*` limits are enforced by the backend to bound resource use —
statevector simulation grows exponentially with the qubit count.

### Running on real quantum hardware

Set `QCB_ENABLE_QUANTUM_HW=true` to reveal the run toggle, then choose a cloud
provider with `QCB_QUANTUM_PROVIDER` — `ibm` (IBM Quantum via Qiskit Runtime) or
`braket` (AWS Braket). Both default to a no-credentials **local simulator**, so
the quantum path works without an account on either; add credentials only to
reach a real device.

**IBM Quantum (`QCB_QUANTUM_PROVIDER=ibm`, the default):**

1. Create a free account at <https://quantum.cloud.ibm.com> and copy your **API
   key** and **instance** (CRN). IBM's free Open Plan includes ~10 minutes of
   QPU time per month.
2. In `.env`, set `QCB_ENABLE_QUANTUM_HW=true`, paste `QCB_IBM_TOKEN` and
   `QCB_IBM_INSTANCE`, and set `QCB_IBM_BACKEND=least_busy` (or a device name).
3. Restart the server, switch the toggle to **Quantum hardware**, and press
   **Run**. Jobs queue, so a run can take a while. Keep `QCB_IBM_BACKEND=aer` to
   exercise the same path locally without spending QPU time.

**AWS Braket (`QCB_QUANTUM_PROVIDER=braket`):**

1. Install the SDK: `pip install amazon-braket-sdk` (or uncomment it in
   `requirements.txt` and re-run `make install`). It's lazily imported, so the
   app runs fine without it until you select the Braket provider.
2. In `.env`, set `QCB_ENABLE_QUANTUM_HW=true` and `QCB_QUANTUM_PROVIDER=braket`.
   Leave `QCB_BRAKET_DEVICE=local` to use the on-machine `LocalSimulator` (no AWS
   account, instant, counts-only) — great for testing the path without spending.
3. For a real device, set `QCB_BRAKET_DEVICE` to an AWS Braket device ARN (an
   on-demand simulator such as
   `arn:aws:braket:::device/quantum-simulator/amazon/sv1`, or a QPU ARN).
   Real devices bill your AWS account and may queue. They authenticate via the
   standard AWS credential chain (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
   or a shared profile) and `AWS_REGION` — set those in your shell/AWS config;
   this app never reads or stores AWS secrets.
4. Restart the server, switch the toggle to **Quantum hardware**, and press
   **Run**.

> Note: real hardware (and the local-simulator paths) returns **measurement counts only** —
> amplitudes and Bloch vectors aren't physically measurable. For convenience the
> Statevector and Bloch tabs still populate on a hardware run, but they're
> computed from a **local simulation** of the same circuit and labeled as such.
> Credentials live in `.env`, which is gitignored; never commit them.

### Ask the Professor (AI circuit explainer + Q&A)

Enable a **Professor** tab — a friendly quantum-computing instructor that explains,
in plain language, what the current circuit does and why it produces its
distribution. You can also ask it **anything about quantum computing** — not just
the circuit on the canvas. Ask about the circuit (e.g. "why are only even outcomes
showing up?") and the answer is grounded in your live gate list and outcomes; ask a
broader question (an algorithm, a concept, the hardware) and it answers fully on its
own terms, reaching for your circuit only when it makes a helpful example. When a
question is genuinely ambiguous it asks one short clarifying question first. To keep
answers accurate, a small **curated reference set** is pulled into the prompt: the
gates and foundational concepts implied by your circuit (a direct lookup) plus, for a
free-text question, the most relevant notes on algorithms, concepts, and hardware
found by an **offline TF-IDF retriever** — no embeddings, no extra requests, and it
stays quiet when nothing in the set is relevant. The
**conversation is remembered**, so you can ask follow-ups; press **Clear chat** to
start fresh. Every exchange keeps a **thumbnail of the circuit** it was about
(snapshotted when you asked, so later edits don't change it — click to enlarge)
and a **timestamp**. Answers are formatted on the fly: inline **Markdown**
emphasis and **LaTeX bra-ket math** (kets, fractions, tensor products, square
roots) render via a small hand-written renderer, so the project stays
build-step- and dependency-free. **Explain this circuit** also works on a blank
canvas — it explains the `|0…0⟩` ground state as a starting lesson.

A **Persona** dropdown lets you switch who's explaining. It holds 73 personas,
each with its own **custom SVG avatar** (drawn inline as a recognizable caricature
of that figure — no image files, no extra requests) and a **hover tooltip**
describing who they are: real people get a short factual bio of their scientific
contributions, while fictional characters get an in-universe blurb in their own
voice. The Professor is always first; everyone else is sorted alphabetically. The
roster includes real scientists (Feynman, Einstein, Marie Curie, Newton, Darwin,
Bohr, Faraday, Tesla, Ada Lovelace, Carl Sagan, Neil deGrasse Tyson), pop-science
and superhero figures (Tony Stark, Hank Pym, Dr. Manhattan, Batman, Captain Marvel,
Bob Ross), and a deep sci-fi/pop-culture bench from Star Trek (Spock, Picard,
Data), Star Wars (Yoda, Obi-Wan, Darth Vader, Jar Jar), Stargate (Samantha Carter,
Daniel Jackson, Jack O'Neill), the Muppets (Kermit, Beaker), Firefly (Kaylee), and
even Schrödinger's cat. A couple of personas (Elon Musk, Jar Jar) are deliberately *unreliable* for
comedy and are clearly framed as such, and Beaker — true to form — only ever answers
in a flurry of panicked meeps. Otherwise each persona is just a different *voice*;
the physics stays correct and grounded in your real circuit. (Persona voices and
blurbs live entirely on the server — the browser only sends the chosen key — so
they can't be used to inject prompts.)

You can run the Professor on **Claude**, **Gemini**, or **ChatGPT** (cloud, each
needs a key) or a **local Llama** model via Ollama (no API key, nothing leaves your
machine). Configure one provider the simple way, or enable several and switch
between them — and between their models — using the **Provider** and **Model**
dropdowns in the Professor header. Only providers you've enabled (and that have a
key, where one is needed) ever appear; every key stays server-side.

**Option A — Claude (default):**

1. Get a Claude API key at <https://platform.claude.com> → **API Keys**.
2. In `.env`, set `QCB_ENABLE_AI=true` and paste `QCB_AI_API_KEY` (optionally
   change `QCB_AI_MODEL`). The provider defaults to `anthropic`.
3. Restart the server, open the **Professor** tab, and press **Explain this
   circuit** — or ask a question in the box and press **Ask**.

To run on **Gemini** or **ChatGPT** instead, set `QCB_AI_PROVIDER=gemini` (key from
<https://aistudio.google.com/app/apikey>, e.g. `QCB_AI_MODEL=gemini-2.0-flash`) or
`QCB_AI_PROVIDER=openai` (key from <https://platform.openai.com/api-keys>, e.g.
`QCB_AI_MODEL=gpt-4o`), and put the key in `QCB_AI_API_KEY`.

**Offer several providers at once:** set `QCB_PROVIDER_ANTHROPIC=true`,
`QCB_PROVIDER_GEMINI=true`, `QCB_PROVIDER_OPENAI=true`, and/or
`QCB_PROVIDER_LLAMA=true`, giving each its own `QCB_<NAME>_API_KEY` (and optionally
`QCB_<NAME>_MODEL` / `QCB_<NAME>_MODELS`). The UI then shows a **Provider** picker
and a **Model** picker, and you choose per request.

**Option B — local Llama via Ollama (no key):**

1. Install [Ollama](https://ollama.com) and start it with `ollama serve`.
2. Pull a model, then set `QCB_AI_MODEL` to that exact name. Any chat model
   Ollama can run works — pick one and match the name:
   - `ollama pull llama3.2` → `QCB_AI_MODEL=llama3.2` — **3B, fastest; recommended
     on a CPU-only machine.**
   - `ollama pull llama3` → `QCB_AI_MODEL=llama3` — 8B, noticeably smarter but
     slower (≈2 tokens/sec on a typical laptop CPU, so a full explanation can take
     a couple of minutes).
   - `ollama pull mistral` → `QCB_AI_MODEL=mistral`, etc.
3. In `.env`, set `QCB_ENABLE_AI=true`, `QCB_AI_PROVIDER=llama`, and
   `QCB_AI_MODEL=<the name you pulled>`. No API key is needed. If Ollama runs
   elsewhere, point `QCB_AI_LLAMA_HOST` at it (default `http://localhost:11434`).
4. Restart the server and use the **Professor** tab as above.

> The Professor talks to Ollama with the Python standard library only (no extra
> dependency), and gives a local model up to 5 minutes to reply — the first call
> after `ollama serve` is slowest because the model has to load into memory.
> Bigger models are smarter but slower on a CPU; if explanations time out, switch
> to a smaller model (e.g. `llama3.2`) or run Ollama on a machine with a GPU.

Either way, the backend runs the simulation itself and sends the model the gate
list, the exact circuit as OpenQASM 3, and the real outcome distribution (plus
your question and prior conversation), so the answer stays grounded. Every API key
is read from `.env` only — it is never returned by `/config` or `/explain`.

## Make targets

| Target         | Description                                  |
| -------------- | -------------------------------------------- |
| `make install` | Create `.venv` and install dependencies.     |
| `make run`     | Start the server.                            |
| `make dev`     | Start with auto-reload.                      |
| `make test`    | Run **all** tests (backend + frontend).      |
| `make test-backend`  | Run only the backend (pytest) tests.   |
| `make test-frontend` | Run only the frontend (`node --test`) tests. |
| `make clean`   | Remove `.venv` and Python caches.            |

## Tests

There are two suites — `make test` runs both, or run one with `make test-backend`
/ `make test-frontend`.

**Backend** — a [pytest](https://docs.pytest.org) suite under `backend/tests/` covers the
security boundary (gate whitelist + resource bounds in `validate`), circuit
correctness — including Qiskit's little-endian convention — the AI provider and
persona registries, the export/codegen helpers (Qiskit + OpenQASM 3, angle
formatting, the grounded explainer prompt), the curated reference set behind the
explainer (every whitelisted gate has a note; retrieval pulls the right notes,
always includes the foundational concepts, dedupes, and stays within its size
bounds), and the HTTP endpoints (`/config`, `/simulate`, `/export`, `/explain`). It
is split by concern: `test_core.py` and `test_export.py` exercise the domain logic
in `backend/core.py` directly, `test_knowledge.py` covers the reference set and
retrieval in `backend/knowledge.py`, while `test_api.py` drives the FastAPI routes
through a `TestClient` (shared fixtures live in `conftest.py`). The tests never make a real network, LLM, or quantum call:
the `/explain` cases exercise the validation paths that run *before* any provider
is contacted (or dispatch through a fake handler), and a key check asserts no API
key ever appears in `/config`.

**Frontend** — a suite under `frontend/tests/` exercises the pure UI logic (the gate
catalog and algorithm presets, the bra-ket LaTeX + Markdown renderer, the circuit
state mutations, and the run-queue bookkeeping), with happy- and sad-path cases for
each. To honor the project's no-build-step / no-npm rule, it uses **Node's built-in
test runner** (`node --test`, no dependencies): a small harness in
`frontend/tests/harness.mjs` loads the real `frontend/*.js` source files into a
sandboxed `vm` context with a stub DOM, so the same code the browser runs is tested
directly. Requires Node 18+ (only for the tests — the app itself ships no JS
toolchain).

```bash
make test            # both suites
make test-backend    # or: .venv/bin/python -m pytest backend
make test-frontend   # or: node --test frontend/tests/*.test.mjs
```
