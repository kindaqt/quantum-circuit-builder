"""HTTP layer for the Quantum Circuit Playground.

Defines the FastAPI app, the request models that are specific to the wire format
(`ChatTurn`, `ExplainSpec`), and the four routes (`/explain`, `/config`,
`/simulate`, `/export`). All of the actual work — validation, circuit building,
simulation, the quantum run paths, personas, and the AI dispatch — lives in
`core`; the routes are thin adapters over it.

Domain symbols are always referenced as ``core.<name>`` rather than imported by
name. That keeps the routes honest about where logic lives, and it means tests
can monkeypatch ``core.<name>`` (e.g. ``core.default_provider``) and have the
running routes pick up the patch.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import the domain module in a way that works both as a package (uvicorn loads
# this as ``backend.api``) and as a top-level module (pytest puts ``backend/`` on
# sys.path and imports ``core`` directly).
try:
    from . import core, db
except ImportError:  # pragma: no cover - exercised by the top-level import path
    import core
    import db


app = FastAPI(title="Quantum Circuit Playground")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


# ---- Request models specific to the /explain wire format -------------------
class ChatTurn(BaseModel):
    role: str
    content: str


class ExplainSpec(core.CircuitSpec):
    question: str | None = None
    history: list[ChatTurn] = []
    persona: str | None = None
    provider: str | None = None
    model: str | None = None


def _clean_history(turns: list[ChatTurn]) -> list[dict]:
    """Validate and bound the prior conversation before it reaches the model."""
    if len(turns) > core.MAX_HISTORY_TURNS:
        raise HTTPException(422, f"conversation too long (max {core.MAX_HISTORY_TURNS} turns)")
    out: list[dict] = []
    total = 0
    for t in turns:
        if t.role not in ("user", "assistant"):
            raise HTTPException(422, "history role must be 'user' or 'assistant'")
        content = t.content or ""
        total += len(content)
        if total > core.MAX_HISTORY_CHARS:
            raise HTTPException(422, "conversation history is too large")
        out.append({"role": t.role, "content": content})
    return out


@app.post("/explain")
def explain(spec: ExplainSpec):
    """Return a plain-language explanation of the circuit — or an answer to the
    student's specific question about it — from the configured LLM, carrying any
    prior conversation for continuity."""
    if not core.ai_enabled():
        raise HTTPException(403, "AI explainer is disabled (set QCB_ENABLE_AI=true and configure a provider)")
    core.validate(spec)
    question = (spec.question or "").strip()
    if len(question) > core.MAX_QUESTION_CHARS:
        raise HTTPException(422, f"question too long (max {core.MAX_QUESTION_CHARS} characters)")
    # The persona is just a key into the server-side registry; reject anything not
    # in it so a client can never smuggle in its own voice/system prompt.
    persona = (spec.persona or core.DEFAULT_PERSONA)
    if persona not in core.PERSONAS:
        raise HTTPException(422, f"unknown persona '{persona}'")
    # Resolve and validate provider + model against the server-side registry, so a
    # client can only ever pick from what's actually enabled (never smuggle a key).
    provider = (spec.provider or core.default_provider())
    if not provider or not core.provider_ready(provider):
        raise HTTPException(422, f"unknown or disabled provider '{spec.provider}'")
    model = (spec.model or core.PROVIDERS[provider]["default_model"])
    if model not in core.PROVIDERS[provider]["models"]:
        raise HTTPException(422, f"unknown model '{model}' for provider '{provider}'")
    history = _clean_history(spec.history)
    explanation = core.explain_circuit(spec, question or None, history, persona, provider, model)
    # Never return the API key — only the explanation and which model/persona produced it.
    return {"explanation": explanation, "provider": provider, "model": model, "persona": persona}


@app.get("/config")
def config():
    """Tell the frontend which optional features are enabled (never leaks secrets)."""
    enabled = core.enabled_providers()
    ai_on = bool(enabled)
    return {
        "quantum_enabled": core.ENABLE_QUANTUM_HW,
        "quantum_provider": core.QUANTUM_PROVIDER if core.ENABLE_QUANTUM_HW else None,
        "backend": core._quantum_target_label() if core.ENABLE_QUANTUM_HW else None,
        # Max concurrent quantum-hardware runs the UI queue allows before it
        # disables the Run button (env QCB_QUEUE_MAX).
        "queue_max": core.QUEUE_MAX,
        "ai_enabled": ai_on,
        # The enabled AI providers, each with its offered model list + default. Only
        # providers that are toggled on (and have a key, if needed) appear, so the UI
        # shows exactly what's usable. Keys themselves are never included.
        "ai_providers": [
            {
                "key": n,
                "label": core.PROVIDERS[n]["label"],
                "models": core.PROVIDERS[n]["models"],
                "default_model": core.PROVIDERS[n]["default_model"],
            }
            for n in enabled
        ],
        "default_provider": core.default_provider(),
        # Backward-compatible single-model hint (the default provider's default model).
        "ai_model": core.PROVIDERS[core.default_provider()]["default_model"] if ai_on else None,
        # The professor's selectable personas (key + display name + avatar emoji).
        # Voices live server-side; the client only ever echoes a key back to /explain.
        "personas": [
            {"key": k, "name": p["name"], "blurb": p.get("blurb", "")}
            for k, p in core.PERSONAS.items()
        ] if ai_on else [],
        "default_persona": core.DEFAULT_PERSONA,
        # Let the UI cap its qubit stepper at whatever the backend will actually
        # simulate (statevector cost is exponential, so this stays bounded).
        "max_qubits": core.MAX_QUBITS,
    }


@app.get("/health")
def health():
    """Liveness + optional-database status.

    The app is always 'ok' (the core playground needs no database); the `db`
    block reports whether the optional memory layer is wired up and reachable,
    which is what `make db-up` / `make migrate` and manual testing check.
    """
    return {"status": "ok", "db": db.status()}


@app.post("/simulate")
def simulate(spec: core.CircuitSpec):
    core.validate(spec)
    n = spec.num_qubits

    if spec.mode in ("qsim", "quantum"):
        counts, label = core.run_quantum(spec, force_aer=(spec.mode == "qsim"))
        # The device returns counts only. Fill the statevector/Bloch views from a
        # local simulation of the same circuit and flag them as simulated so the
        # UI can say so plainly.
        statevector, bloch, _ = core._simulated_state(spec)
        return {
            "counts": counts,
            "statevector": statevector,
            "bloch": bloch,
            "backend": label,
            "mode": spec.mode,
            "extras_simulated": True,
        }

    # Classical "sim" mode: the statevector and Bloch views are computed exactly
    # (they're not measurable on real hardware anyway), but the measurement
    # histogram comes from a real local Aer run — the same simulator the qsim/quantum
    # paths use — so "Classical simulator" is a genuine shot-based measurement rather
    # than a numpy sampling of the exact distribution. If Aer isn't installed we fall
    # back to sampling that distribution, so the core classical path always works.
    statevector, bloch, probs = core._simulated_state(spec)
    try:
        counts, backend = core.run_quantum(spec, force_aer=True)
    except HTTPException:
        sampled = core.np.random.multinomial(spec.shots, probs)
        counts = {f"{i:0{n}b}": int(c) for i, c in enumerate(sampled) if c > 0}
        backend = "statevector (local)"
    return {
        "counts": counts,
        "statevector": statevector,
        "bloch": bloch,
        "backend": backend,
        "mode": "sim",
        "extras_simulated": False,
    }


@app.post("/export")
def export(spec: core.CircuitSpec):
    """Return the circuit as both Qiskit Python and OpenQASM 3 for copy-paste.
    Validates first (same whitelist + bounds as every other entry point), so no
    un-vetted gate ever reaches the circuit builder."""
    core.validate(spec)
    qc = core.build_circuit(spec)
    qc.measure_all()
    return {"qiskit": core._qiskit_source(spec), "qasm": core.qasm3_dumps(qc).strip()}


# Serve the static frontend at the root (registered last so /simulate wins).
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
