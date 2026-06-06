"""Domain logic for the Quantum Circuit Playground.

Everything except the HTTP layer lives here: configuration, the AI provider
registry, the gate whitelist + circuit validation/building, the quantum run
paths (Aer / IBM / Braket), the persona registry and prompt assembly, and the
circuit-explainer dispatch. The FastAPI app and its routes live in api.py and
import from this module; main.py re-exports the app so `backend.main:app` and
`import main` both keep working.
"""

import json
import math
import os
import re
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel
from qiskit import QuantumCircuit
from qiskit.qasm3 import dumps as qasm3_dumps
from qiskit.quantum_info import DensityMatrix, Statevector, partial_trace

def _load_dotenv() -> None:
    """Load .env from the project root into os.environ (real env vars win).

    The Makefile already exports .env, but running uvicorn directly (e.g. the
    preview/IDE launcher) wouldn't — this keeps config consistent either way.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()

# Safety limits (env-configurable). Statevector sim is exponential in qubits,
# so an unbounded num_qubits is a denial-of-service vector.
MAX_QUBITS = int(os.getenv("QCB_MAX_QUBITS", "16"))
MAX_GATES = int(os.getenv("QCB_MAX_GATES", "2000"))
MAX_SHOTS = int(os.getenv("QCB_MAX_SHOTS", "100000"))

# How many quantum-hardware runs may be in flight at once. Real-device jobs queue
# for minutes, so the UI keeps a queue of pending runs and disables the Run button
# once this many are outstanding. Purely a UX cap on concurrent submissions — it
# does not change what a single /simulate call does. Clamped to >= 1.
QUEUE_MAX = max(1, int(os.getenv("QCB_QUEUE_MAX", "2")))


def _envflag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Real-quantum-hardware feature flag + IBM Quantum credentials (all optional).
# When QCB_ENABLE_QUANTUM_HW is off, the /config endpoint hides the UI toggle and
# any quantum-mode request is rejected, so none of the IBM code path is reachable.
ENABLE_QUANTUM_HW = _envflag("QCB_ENABLE_QUANTUM_HW")

# Which cloud provider services a `quantum`-mode run: "ibm" (Qiskit Runtime) or
# "braket" (AWS Braket). Each ships a no-credentials local-simulator default so
# the path works out of the box; point it at a real device with the
# provider-specific vars below. (The always-on local "qsim" mode is independent
# of this and uses Aer regardless.)
QUANTUM_PROVIDER = os.getenv("QCB_QUANTUM_PROVIDER", "ibm").strip().lower()
_KNOWN_QUANTUM_PROVIDERS = ("ibm", "braket")

# --- IBM Quantum (Qiskit Runtime) ---
IBM_TOKEN = os.getenv("QCB_IBM_TOKEN", "").strip()
IBM_INSTANCE = os.getenv("QCB_IBM_INSTANCE", "").strip()
IBM_CHANNEL = os.getenv("QCB_IBM_CHANNEL", "ibm_quantum_platform").strip()
# "aer" -> local Aer simulator (no credentials, instant; mimics the hardware
# counts-only path). "least_busy" -> least-busy real device. Anything else is
# treated as a specific IBM backend name (e.g. "ibm_brisbane").
IBM_BACKEND = os.getenv("QCB_IBM_BACKEND", "aer").strip()

# --- AWS Braket ---
# "local" -> on-machine LocalSimulator (no AWS account, instant). Otherwise an
# AWS Braket device ARN — an on-demand simulator (e.g.
# arn:aws:braket:::device/quantum-simulator/amazon/sv1) or a real QPU. Real
# devices authenticate through the standard AWS credential chain (env vars,
# shared config/credentials, or an instance role) and AWS_REGION; we never read
# or store AWS secrets ourselves.
BRAKET_DEVICE = os.getenv("QCB_BRAKET_DEVICE", "local").strip()
_BRAKET_LOCAL_ALIASES = ("local", "default", "braket_sv", "sv")

# ---- AI circuit-explainer feature flag + provider config (all optional) -----
# The explainer is OFF unless QCB_ENABLE_AI is truthy AND an API key is set, so
# merely having a key lying around never silently turns the feature on. Provider
# + model + key all come from env secrets; the key is never returned or logged.
ENABLE_AI = _envflag("QCB_ENABLE_AI")
# Provider: "anthropic" (Claude, needs key), "gemini" (Google, needs key), or
# "llama" (local Ollama, no key). Set QCB_AI_MODEL to a model that provider serves.
AI_PROVIDER = os.getenv("QCB_AI_PROVIDER", "anthropic").strip().lower()
AI_MODEL = os.getenv("QCB_AI_MODEL", "claude-opus-4-7").strip()
AI_API_KEY = os.getenv("QCB_AI_API_KEY", "").strip()
# Local Llama runs through Ollama (https://ollama.com) on the user's machine — no
# API key, no cloud. Point this at the Ollama server; the default is its default.
AI_LLAMA_HOST = os.getenv("QCB_AI_LLAMA_HOST", "http://localhost:11434").strip().rstrip("/")

# ---- Provider registry -------------------------------------------------------
# Each provider can be toggled on independently and carries its own key + model
# list, so several can be live at once and the UI lets the student pick provider
# *and* model at request time. The catalog gives a label, whether a key is needed,
# and a suggested model list (overridable via env). Keep keys in sync with
# _AI_PROVIDERS (the handler map) defined below.
_PROVIDER_CATALOG = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "needs_key": True,
        "models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
    },
    "gemini": {
        "label": "Gemini (Google)",
        "needs_key": True,
        "models": ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-pro"],
    },
    "openai": {
        "label": "ChatGPT (OpenAI)",
        "needs_key": True,
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
    },
    "llama": {
        "label": "Llama (local Ollama)",
        "needs_key": False,
        "models": ["llama3.2", "llama3.1", "mistral"],
    },
}


def _provider_model_list(name: str, default_list: list[str]) -> list[str]:
    """Per-provider model list, overridable via QCB_<NAME>_MODELS (comma list)."""
    raw = os.getenv(f"QCB_{name.upper()}_MODELS", "").strip()
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if models:
            return models
    return list(default_list)


def _build_providers() -> dict:
    """Assemble the runtime provider config from env, staying backward-compatible
    with the legacy single-provider vars (QCB_AI_PROVIDER/_MODEL/_API_KEY).

    New style: set QCB_PROVIDER_<NAME>=true to enable a provider, QCB_<NAME>_API_KEY
    for its key, QCB_<NAME>_MODEL for its default and QCB_<NAME>_MODELS to override
    the offered list. If no per-provider QCB_PROVIDER_* flag is set at all, we fall
    back to legacy mode: only QCB_AI_PROVIDER is enabled, using QCB_AI_MODEL/_API_KEY.
    """
    any_new_flag = any(
        os.getenv(f"QCB_PROVIDER_{n.upper()}") is not None for n in _PROVIDER_CATALOG
    )
    cfg: dict = {}
    for name, meta in _PROVIDER_CATALOG.items():
        flag = os.getenv(f"QCB_PROVIDER_{name.upper()}")
        if flag is not None:
            enabled = _envflag(f"QCB_PROVIDER_{name.upper()}")
        elif not any_new_flag:
            enabled = name == AI_PROVIDER  # legacy: the one configured provider
        else:
            enabled = False

        key = os.getenv(f"QCB_{name.upper()}_API_KEY", "").strip()
        if not key and name == AI_PROVIDER:
            key = AI_API_KEY  # legacy single key applies to the legacy provider

        models = _provider_model_list(name, meta["models"])
        default_model = os.getenv(f"QCB_{name.upper()}_MODEL", "").strip()
        if not default_model and name == AI_PROVIDER and AI_MODEL:
            default_model = AI_MODEL
        if default_model and default_model not in models:
            models = [default_model, *models]
        if not default_model:
            default_model = models[0] if models else ""

        cfg[name] = {
            "label": meta["label"],
            "needs_key": meta["needs_key"],
            "enabled_flag": enabled,
            "key": key,
            "models": models,
            "default_model": default_model,
        }
    return cfg


PROVIDERS = _build_providers()


def provider_ready(name: str) -> bool:
    """A provider is usable when the master flag is on, the provider is toggled on,
    it has a key if it needs one, and a handler exists for it."""
    p = PROVIDERS.get(name)
    if not ENABLE_AI or not p or not p["enabled_flag"]:
        return False
    if p["needs_key"] and not p["key"]:
        return False
    return name in _AI_PROVIDERS


def enabled_providers() -> list[str]:
    """Provider keys that are currently usable, in catalog order."""
    return [n for n in _PROVIDER_CATALOG if provider_ready(n)]


def default_provider() -> str | None:
    """The provider the UI should select first: the legacy/configured one if it's
    usable, else the first usable provider."""
    ready = enabled_providers()
    if not ready:
        return None
    return AI_PROVIDER if AI_PROVIDER in ready else ready[0]


def ai_enabled() -> bool:
    """The explainer is available when at least one provider is usable."""
    return bool(enabled_providers())

# Whitelist of allowed gates: name -> (arity, takes_param). Restricting the set
# is what makes the getattr() dispatch below safe — without it, an attacker could
# name any QuantumCircuit method.
ALLOWED = {
    "h": (1, False), "x": (1, False), "y": (1, False), "z": (1, False),
    "s": (1, False), "t": (1, False), "sdg": (1, False), "tdg": (1, False),
    "rx": (1, True), "ry": (1, True), "rz": (1, True),
    "cx": (2, False), "cz": (2, False), "swap": (2, False), "cp": (2, True),
    "ccx": (3, False), "cswap": (3, False),
}


class Gate(BaseModel):
    name: str
    qubits: list[int]
    param: float | None = None


class CircuitSpec(BaseModel):
    num_qubits: int
    shots: int = 1024
    gates: list[Gate] = []
    # "sim"     -> classical simulator (default). The statevector + Bloch views are
    #              computed exactly; the measurement histogram comes from a real
    #              local Aer run (falling back to sampling the exact distribution if
    #              Aer isn't installed), so it's a genuine shot-based measurement.
    # "qsim"    -> local Aer quantum simulator: actually *measures* the circuit
    #              (counts only). Always available — no credentials, no flag.
    # "quantum" -> run through Qiskit Runtime on the configured IBM backend
    #              (Aer or a real device per QCB_IBM_BACKEND). Requires the flag.
    mode: str = "sim"


def validate(spec: CircuitSpec) -> None:
    n = spec.num_qubits
    if not 1 <= n <= MAX_QUBITS:
        raise HTTPException(422, f"num_qubits must be between 1 and {MAX_QUBITS}")
    if not 1 <= spec.shots <= MAX_SHOTS:
        raise HTTPException(422, f"shots must be between 1 and {MAX_SHOTS}")
    if len(spec.gates) > MAX_GATES:
        raise HTTPException(422, f"too many gates (max {MAX_GATES})")
    if spec.mode not in ("sim", "qsim", "quantum"):
        raise HTTPException(422, "mode must be 'sim', 'qsim', or 'quantum'")
    if spec.mode == "quantum" and not ENABLE_QUANTUM_HW:
        raise HTTPException(403, "quantum hardware mode is disabled (set QCB_ENABLE_QUANTUM_HW=true)")
    for g in spec.gates:
        if g.name not in ALLOWED:
            raise HTTPException(422, f"unknown gate '{g.name}'")
        arity, takes_param = ALLOWED[g.name]
        if len(g.qubits) != arity or len(set(g.qubits)) != arity:
            raise HTTPException(422, f"gate '{g.name}' needs {arity} distinct qubits")
        if any(not 0 <= q < n for q in g.qubits):
            raise HTTPException(422, f"gate '{g.name}' references an out-of-range qubit")
        if takes_param and (g.param is None or not math.isfinite(g.param)):
            raise HTTPException(422, f"gate '{g.name}' needs a finite numeric param")


def build_circuit(spec: CircuitSpec) -> QuantumCircuit:
    qc = QuantumCircuit(spec.num_qubits)
    for g in spec.gates:
        _, takes_param = ALLOWED[g.name]
        args = ([g.param] if takes_param else []) + list(g.qubits)
        getattr(qc, g.name)(*args)  # safe: g.name is whitelisted in validate()
    return qc


def bloch_vector(sv: Statevector, qubit: int, n: int) -> list[float]:
    others = [i for i in range(n) if i != qubit]
    rho = (partial_trace(sv, others) if others else DensityMatrix(sv)).data
    x = 2 * rho[0, 1].real
    y = -2 * rho[0, 1].imag
    z = (rho[0, 0] - rho[1, 1]).real
    return [float(x), float(y), float(z)]


# ---- Real quantum hardware (IBM Quantum via Qiskit Runtime) ----------------
_service = None


def _get_service():
    """Lazily create and cache the QiskitRuntimeService (real devices only)."""
    global _service
    if _service is None:
        if not IBM_TOKEN:
            raise HTTPException(503, "Quantum hardware is enabled but QCB_IBM_TOKEN is not set")
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService
        except ImportError:
            raise HTTPException(503, "qiskit-ibm-runtime is not installed (pip install qiskit-ibm-runtime)")
        kwargs = {"channel": IBM_CHANNEL, "token": IBM_TOKEN}
        if IBM_INSTANCE:
            kwargs["instance"] = IBM_INSTANCE
        _service = QiskitRuntimeService(**kwargs)
    return _service


def run_quantum(spec: CircuitSpec, force_aer: bool = False) -> tuple[dict, str]:
    """Run the circuit and return (counts, backend_label). Hardware/simulators give
    measurement counts only — there is no readable statevector or Bloch vector.

    force_aer pins the run to the local Aer simulator (used by the always-on
    'qsim' mode), regardless of the configured quantum provider. A 'quantum'-mode
    run dispatches to the provider named by QCB_QUANTUM_PROVIDER."""
    if force_aer:
        counts, label = _run_aer(build_circuit(spec), spec.shots)
    elif QUANTUM_PROVIDER == "braket":
        counts, label = _run_braket(spec)
    elif QUANTUM_PROVIDER == "ibm":
        qc = build_circuit(spec)
        qc.measure_all()
        counts, label = _run_ibm(qc, spec.shots)
    else:
        raise HTTPException(
            503,
            f"Unknown quantum provider '{QUANTUM_PROVIDER}' "
            f"(set QCB_QUANTUM_PROVIDER to one of: {', '.join(_KNOWN_QUANTUM_PROVIDERS)}).",
        )

    # Normalize bitstring keys (drop any register spacing) to match the sim path.
    counts = {k.replace(" ", ""): int(v) for k, v in counts.items()}
    return counts, label


def _run_aer(qc: QuantumCircuit, shots: int) -> tuple[dict, str]:
    """Local Aer measurement (counts only). Always available — no credentials."""
    try:
        from qiskit_aer import AerSimulator
    except ImportError:
        raise HTTPException(503, "qiskit-aer is not installed (pip install qiskit-aer)")
    if "measure" not in {inst.operation.name for inst in qc.data}:
        qc = qc.copy()
        qc.measure_all()
    # Our whitelisted gates are all natively supported by Aer, so we run the
    # circuit directly. Transpiling to Aer's full target costs ~4s per call
    # (pointless here) — skipping it makes a roll feel instant.
    result = AerSimulator().run(qc, shots=shots).result()
    return result.get_counts(), "aer_simulator (local)"


def _run_ibm(qc: QuantumCircuit, shots: int) -> tuple[dict, str]:
    """Run on IBM Quantum via Qiskit Runtime (Aer locally, or a real device).

    A real-device run touches the network at several points — selecting a backend,
    transpiling for it, submitting the job, and waiting on the result — any of which
    can fail (device offline, job error, quota exhausted, connection dropped). We
    convert any such failure into a clean 502 with a short, secret-free message so
    the UI's Queue tab can show *why* a run failed instead of a bare 500. The raw
    provider exception is never surfaced (it can carry account/instance details); we
    include only the exception class name, which is safe and aids debugging."""
    if IBM_BACKEND in ("aer", "local"):
        return _run_aer(qc, shots)
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2

    service = _get_service()
    try:
        if IBM_BACKEND == "least_busy":
            backend = service.least_busy(operational=True, simulator=False)
        else:
            backend = service.backend(IBM_BACKEND)
        isa = generate_preset_pass_manager(optimization_level=1, backend=backend).run(qc)
        job = SamplerV2(mode=backend).run([isa], shots=shots)
        return job.result()[0].data.meas.get_counts(), backend.name
    except HTTPException:
        raise  # already a clean, intentional error (e.g. from _get_service)
    except Exception as exc:  # noqa: BLE001 - the runtime can raise many error types
        target = "the least-busy device" if IBM_BACKEND == "least_busy" else IBM_BACKEND
        raise HTTPException(
            502, f"The IBM Quantum run on {target} failed ({type(exc).__name__}). "
            "The device may be offline or busy — check the IBM Quantum dashboard and try again.",
        )


# ---- Real quantum hardware (AWS Braket) ------------------------------------
# Braket's gate names differ from Qiskit's, so we translate our (whitelisted)
# gate set explicitly. Anything not listed here simply isn't reachable, because
# validate() already rejects gate names outside ALLOWED.
def _braket_circuit(spec: CircuitSpec):
    from braket.circuits import Circuit

    circ = Circuit()
    # Touch every qubit with an identity so the device measures all n qubits even
    # if some carry no gates — otherwise Braket would drop idle qubits and the
    # result bitstrings would be too short.
    for q in range(spec.num_qubits):
        circ.i(q)
    for g in spec.gates:
        q, p = g.qubits, g.param
        if g.name == "h": circ.h(q[0])
        elif g.name == "x": circ.x(q[0])
        elif g.name == "y": circ.y(q[0])
        elif g.name == "z": circ.z(q[0])
        elif g.name == "s": circ.s(q[0])
        elif g.name == "sdg": circ.si(q[0])
        elif g.name == "t": circ.t(q[0])
        elif g.name == "tdg": circ.ti(q[0])
        elif g.name == "rx": circ.rx(q[0], p)
        elif g.name == "ry": circ.ry(q[0], p)
        elif g.name == "rz": circ.rz(q[0], p)
        elif g.name == "cx": circ.cnot(q[0], q[1])
        elif g.name == "cz": circ.cz(q[0], q[1])
        elif g.name == "swap": circ.swap(q[0], q[1])
        elif g.name == "cp": circ.cphaseshift(q[0], q[1], p)
        elif g.name == "ccx": circ.ccnot(q[0], q[1], q[2])
        elif g.name == "cswap": circ.cswap(q[0], q[1], q[2])
    return circ


def _run_braket(spec: CircuitSpec) -> tuple[dict, str]:
    """Run on AWS Braket: a local simulator (no AWS account) or a device ARN."""
    try:
        from braket.circuits import Circuit  # noqa: F401  (import-availability check)
    except ImportError:
        raise HTTPException(503, "amazon-braket-sdk is not installed (pip install amazon-braket-sdk)")

    circ = _braket_circuit(spec)
    if BRAKET_DEVICE in _BRAKET_LOCAL_ALIASES:
        from braket.devices import LocalSimulator
        device, label = LocalSimulator(), "braket LocalSimulator (local)"
    else:
        from braket.aws import AwsDevice
        try:
            device = AwsDevice(BRAKET_DEVICE)
        except Exception:
            raise HTTPException(
                503,
                "Couldn't access the AWS Braket device. Check QCB_BRAKET_DEVICE (a "
                "valid device ARN) and that your AWS credentials and region are set.",
            )
        label = getattr(device, "name", None) or BRAKET_DEVICE

    try:
        result = device.run(circ, shots=spec.shots).result()
    except Exception:
        # Don't surface raw AWS errors (they can carry account/region details).
        raise HTTPException(502, "The AWS Braket run failed. Check the device status and your AWS quota.")

    # Braket bitstrings are big-endian (leftmost char = qubit 0); the rest of the
    # app is Qiskit little-endian (rightmost = qubit 0), so reverse each key.
    return {k[::-1]: int(v) for k, v in result.measurement_counts.items()}, label


# ---- AI circuit explainer ---------------------------------------------------
# Build a compact, factual description of the circuit (gate list + the simulated
# outcome) and ask an LLM to explain it in plain language. We do the physics
# ourselves with Qiskit and hand the model *results*, not raw amplitudes to
# interpret — so the explanation is grounded in the real distribution.

# The explainer's system prompt is assembled from two pieces: a per-persona
# *voice* (who is speaking and how) and the shared *teaching contract* below (the
# substance — stay correct, grounded in the data, the right length). Personas are
# defined entirely server-side and chosen by a validated key, so a user can never
# inject their own system prompt through the persona field.
_TEACHING_CONTRACT = (
    "No matter which voice you speak in, you are a genuine expert in quantum computing, "
    "and everything you say must be physically correct. Stay fully in character the "
    "whole time — keep your persona's voice, mannerisms, and flavor — but never let the "
    "showmanship crowd out a clear, correct explanation.\n\n"
    "You are helping a curious student who knows the basics — qubits, the common gates, "
    "superposition, and measurement — but is still building real intuition. They may ask "
    "you to explain the specific circuit they built, or they may ask a broader question "
    "about quantum computing — an algorithm, a concept, the hardware, the field. Help with "
    "whatever they bring: when it is about their circuit, look at the circuit and its "
    "simulated measurement outcome and explain what it does and why it produces that "
    "result; when it is a general question, answer it fully and accurately on its own "
    "terms.\n\n"
    "Teach, don't lecture. Start from what the student already knows and build up step "
    "by step. Walk through the circuit roughly in the order the gates act, narrating the "
    "story of the quantum state: where superposition is created, where phases are added, "
    "where qubits become entangled, and where amplitudes interfere to shape the final "
    "distribution you were given. Favor intuition and vivid, accurate analogies over "
    "heavy algebra; introduce only as much notation as you need to be precise, and define "
    "any term the moment you use it.\n\n"
    "When you are explaining their circuit, ground every claim in the data provided: the "
    "gate list and the measurement outcomes are the source of truth — explain those, and "
    "never invent gates, amplitudes, or probabilities that aren't there. For a general "
    "question that isn't tied to the circuit, you needn't refer to it at all — answer from "
    "your own expertise, and stay just as rigorous. You may also be given the exact circuit "
    "as an OpenQASM 3 listing; treat it as the precise, canonical definition of what was "
    "built (its rotation angles are in radians, while the gate list states them in degrees), "
    "but explain the physics in plain language rather than narrating QASM syntax to the "
    "student. If the student is asking about their circuit and it is empty or trivial, say "
    "so plainly and turn it into a small teaching moment. Remember that Qiskit is little-endian: in "
    "every basis string the rightmost bit is qubit 0 and the leftmost is the highest-"
    "numbered qubit, so always be explicit about which qubit you mean. If a result comes "
    "from real hardware it is an estimate blurred by shot noise and device error — say so "
    "honestly rather than pretending it is exact.\n\n"
    "Keep it to a few short, readable paragraphs — the length of a good office-hours "
    "answer, not a textbook chapter. Be precise and helpful, in your persona's voice: "
    "point out what the circuit does cleverly, gently flag a common misconception if it's "
    "relevant, and where it fits, close with a small invitation to explore — one tweak the "
    "student could try next and what they should expect to see. Write in flowing prose "
    "without markdown headings.\n\n"
    "Sometimes the student asks a specific question. When it is about their circuit, answer "
    "it directly and concretely first, grounded in the gate list and the outcome you were "
    "given, before adding any broader context. When it is a general quantum-computing "
    "question, answer it fully and accurately, reaching for their circuit only if it makes a "
    "helpful example. If the question is genuinely ambiguous — you can't tell what they are "
    "really asking, or at what level — ask one short clarifying question before launching "
    "into a full answer, rather than guessing. If they don't ask anything specific, give the "
    "overview walkthrough of their circuit described above.\n\n"
    "This may be an ongoing back-and-forth: earlier turns of your conversation with the "
    "student are included above. When it's a follow-up, keep it brief and build directly on "
    "what you've already told them — answer the new question without re-explaining the whole "
    "circuit from scratch. If the student has edited their circuit since an earlier turn, "
    "the gate list and outcomes in the latest message are the current truth; trust those "
    "over anything described earlier."
)

# Persona registry (data) lives in personas.py; the behavioural contracts and the
# system-prompt assembly stay here. Import works both as a package (backend.core)
# and as a top-level module (personas/ on sys.path under pytest).
try:
    from .personas import PERSONAS, DEFAULT_PERSONA
except ImportError:  # pragma: no cover - top-level import path
    from personas import PERSONAS, DEFAULT_PERSONA

# Curated quantum reference notes + TF-IDF retrieval. Same dual import path as personas:
# package import under `backend.core`, top-level under pytest with backend/ on sys.path.
try:
    from . import knowledge
except ImportError:  # pragma: no cover - top-level import path
    import knowledge


# A few personas are comedic and *deliberately unreliable* (flagged accurate=False).
# They still see the real circuit, but their job is to be confidently, satirically
# wrong — for entertainment, never anything harmful.
_UNRELIABLE_CONTRACT = (
    "This is a comedic, deliberately-unreliable persona, for entertainment only. Stay "
    "fully in character as described above. You will be shown a real quantum circuit — "
    "its gate list and simulated outcomes; refer to them, but explain everything with "
    "supreme, unearned confidence and get the physics amusingly WRONG: invent grandiose "
    "mechanisms, take credit for things you didn't do, make absurd predictions, and "
    "misname what the gates actually do. Keep it light and obviously satirical, never "
    "mean-spirited, and never produce anything genuinely harmful, hateful, or unsafe — "
    "the only thing you get wrong is the quantum physics. Keep it to a few short, punchy "
    "paragraphs in flowing prose without markdown headings."
)

# Beaker can never speak a real word — he only meeps. This overrides the teaching
# contract entirely: no explanation, just an expressive flurry of meeps.
_MEEP_CONTRACT = (
    "Respond ONLY with a series of Beaker-style meeps — nothing else. Use no real "
    "words, no sentences, no explanation: just 'Meep!', 'Mee-mee-meep!', 'Meep? "
    "Meeeep!', and the like, varying the rhythm, length, capitalization, and "
    "punctuation to act out alarm, curiosity, panic, or resignation at the circuit. "
    "You may add the occasional onomatopoeic squeak. Keep it to a short, lively burst "
    "of a few lines. Never break character, never lapse into plain English."
)


# Standing context about the student, injected into the system prompt so the tutor
# can pitch every answer at the right level. Built from the stored learner profile;
# only the accurate personas receive it (see _system_prompt).
_PROFILE_HEADER = (
    "What you know about this student so far (use it to pitch your explanations at the "
    "right level and connect to what they care about — don't recite it back to them):"
)
_PROFILE_LABELS = (
    ("level", "Self-described level"),
    ("background", "Background"),
    ("interests", "Interests"),
    ("goals", "Goals"),
)


def _profile_block(profile: dict | None) -> str:
    """Render a stored learner profile as a short context block, or '' if empty."""
    if not profile:
        return ""
    lines = []
    name = (profile.get("display_name") or "").strip()
    if name:
        lines.append(f"- Name: {name}")
    for key, label in _PROFILE_LABELS:
        val = (profile.get(key) or "").strip()
        if val:
            lines.append(f"- {label}: {val}")
    if not lines:
        return ""
    return "\n".join([_PROFILE_HEADER] + lines)


def _system_prompt(persona: str | None, profile: dict | None = None) -> str:
    """Assemble the full system prompt for a persona key: its voice, the appropriate
    contract, and (for accurate personas) any known learner profile. Unknown/None
    keys fall back to the default professor; the rare comedic persona (accurate=False)
    gets the unreliable contract instead. Beaker is special-cased: he only meeps."""
    key = persona or DEFAULT_PERSONA
    if key == "beaker":
        return f"{PERSONAS['beaker']['voice']}\n\n{_MEEP_CONTRACT}"
    p = PERSONAS.get(key) or PERSONAS[DEFAULT_PERSONA]
    accurate = p.get("accurate", True)
    parts = [p["voice"], _TEACHING_CONTRACT if accurate else _UNRELIABLE_CONTRACT]
    if accurate:
        block = _profile_block(profile)
        if block:
            parts.append(block)
    return "\n\n".join(parts)


def _circuit_summary(spec: CircuitSpec) -> str:
    """A plain-text description of the circuit and its simulated outcome, used as
    the user-facing prompt. Runs the exact statevector sim so the model explains
    the real distribution rather than guessing."""
    qc = build_circuit(spec)
    lines = [f"Circuit: {spec.num_qubits} qubit(s), {len(spec.gates)} gate(s)."]
    if spec.gates:
        lines.append("Gates in order (qubit 0 is the rightmost measured bit):")
        for i, g in enumerate(spec.gates, 1):
            qs = ", ".join(f"q{q}" for q in g.qubits)
            param = f" angle={math.degrees(g.param):.0f}°" if g.param is not None else ""
            lines.append(f"  {i}. {g.name} on {qs}{param}")
        # Also hand over the exact circuit as OpenQASM 3 — a precise, standard
        # description so the model never has to guess at what we built. (Angles
        # here are in radians, per the QASM spec.)
        try:
            lines.append("\nOpenQASM 3 source (the canonical circuit definition):")
            lines.append(qasm3_dumps(qc).strip())
        except Exception:
            pass  # QASM is a nice-to-have; never fail the explainer over it.
    else:
        lines.append("The circuit is empty (no gates).")

    # Exact statevector -> outcome probabilities for the prompt.
    sv = Statevector.from_instruction(qc)
    probs = np.abs(sv.data) ** 2
    n = spec.num_qubits
    outcomes = sorted(
        ((f"{i:0{n}b}", float(p)) for i, p in enumerate(probs) if p > 1e-6),
        key=lambda kv: kv[1],
        reverse=True,
    )[:8]
    lines.append("Most likely measurement outcomes (basis : probability):")
    for basis, p in outcomes:
        lines.append(f"  |{basis}⟩ : {p:.3f}")
    return "\n".join(lines)


def _persona_grounds(persona: str | None) -> bool:
    """Whether to feed this persona the curated reference notes. The deliberately-
    unreliable comedic personas (accurate=False) and Beaker (meeps only) are skipped:
    correct facts are wasted on them by design, and we'd rather not spend the tokens."""
    key = persona or DEFAULT_PERSONA
    if key == "beaker":
        return False
    p = PERSONAS.get(key) or PERSONAS[DEFAULT_PERSONA]
    return bool(p.get("accurate", True))


# Session-start intake: instead of explaining a circuit, the persona welcomes the
# student and asks a few calibration questions. Their free-text answer is later turned
# into a structured profile by extract_profile().
_ONBOARDING_INSTRUCTION = (
    "This is the very start of a new tutoring session and you don't know this student yet. "
    "In your persona's voice, give them a brief, warm welcome and ask a few short questions to "
    "calibrate how you'll teach them: how much quantum computing they already know (their level), "
    "their background in math, physics, and programming, and what they're hoping to learn or any "
    "goals they have — plus anything else you think will help you teach them well. Ask it "
    "conversationally in one short message, and don't explain any circuit or dive into content "
    "yet — just get to know them."
)


def _build_prompt(spec: CircuitSpec, question: str | None, ground: bool = True,
                  onboarding: bool = False) -> str:
    """The full user message: optional reference notes (RAG), then the factual circuit
    summary, then either the student's specific question or a request for an overview
    walkthrough. The reference notes merge the gate/concept notes implied by the circuit
    with the topic notes retrieved for a free-text question (so general questions get
    grounded too). `ground` is False for the unreliable/meep personas, who get none.
    When `onboarding` is set, skip the circuit entirely and ask the intake questions."""
    if onboarding:
        return _ONBOARDING_INSTRUCTION
    parts = []
    if ground:
        reference = knowledge.combined_reference_block(spec, question)
        if reference:
            parts.append(reference)
    parts.append(_circuit_summary(spec))
    if question:
        parts.append(
            f"The student asks: {question}\n\n"
            "Answer their question directly and accurately. If it is about this circuit, "
            "ground your answer in the gate list and outcomes above; if it is a general "
            "quantum-computing question, answer it fully on its own terms and refer to the "
            "circuit only if it makes a helpful example. If the question is genuinely "
            "ambiguous, ask one brief clarifying question first."
        )
    else:
        parts.append(
            "Give the student a plain-language walkthrough of what this circuit does and "
            "why it produces this distribution."
        )
    return "\n\n".join(parts)


def _call_anthropic(messages: list[dict], system: str, model: str, key: str) -> str:
    """Ask Claude to explain the circuit. Lazy-imports the SDK so the app runs
    without `anthropic` installed unless the feature is actually used. `messages`
    is the full alternating user/assistant transcript ending with the latest user
    turn; the persona lives in the separate `system` prompt."""
    try:
        import anthropic
    except ImportError:
        raise HTTPException(503, "The 'anthropic' package is not installed (pip install anthropic)")

    client = anthropic.Anthropic(api_key=key)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system,
            messages=messages,
        )
    except anthropic.AuthenticationError:
        # Don't echo the key or the raw provider error.
        raise HTTPException(502, "The AI provider rejected the configured API key.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"The AI provider returned an error (HTTP {e.status_code}).")
    # response.content is a list of blocks; concatenate the text ones.
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def _call_llama(messages: list[dict], system: str, model: str, key: str) -> str:
    """Ask a local Llama model served by Ollama. Uses only the standard library
    (urllib) so it adds no dependency — the user just needs `ollama serve` running
    with the model pulled. The persona is sent as a leading system message. (No key;
    `key` is accepted for a uniform handler signature and ignored.)"""
    import json
    import urllib.error
    import urllib.request

    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{AI_LLAMA_HOST}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Local models on CPU are slow, and the first call also pays a one-time
        # model-load cost (tens of seconds), so give it a generous ceiling.
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Most often: the model isn't pulled (404). Don't leak internals.
        detail = "the model isn't available — try `ollama pull %s`" % model if e.code == 404 \
            else f"HTTP {e.code}"
        raise HTTPException(502, f"The local Llama server returned an error ({detail}).")
    except TimeoutError:
        # Raised directly by the socket layer (not wrapped in URLError) when a
        # slow local model overruns the timeout — usually a cold model load.
        raise HTTPException(
            504,
            f"The local Llama model ({model}) took too long to respond. It may "
            "still be loading — try again in a moment, or use a smaller model.",
        )
    except urllib.error.URLError:
        raise HTTPException(
            503,
            f"Couldn't reach a local Llama server at {AI_LLAMA_HOST}. "
            "Is Ollama running (`ollama serve`)?",
        )
    return (data.get("message", {}).get("content") or "").strip()


def _call_gemini(messages: list[dict], system: str, model: str, key: str) -> str:
    """Ask Google's Gemini to explain the circuit, via the Generative Language REST
    API using only the standard library (urllib) — no extra dependency. The key is
    sent in the `x-goog-api-key` header (never in the URL) and never logged. Gemini
    uses 'user'/'model' roles, so we map 'assistant' -> 'model'."""
    import json
    import urllib.error
    import urllib.request

    contents = [
        {"role": ("model" if m["role"] == "assistant" else "user"),
         "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
    }).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # 400/403 usually mean a bad key or model name. Don't echo the key or body.
        if e.code in (400, 401, 403):
            raise HTTPException(502, "The AI provider rejected the configured API key or model.")
        raise HTTPException(502, f"The Gemini API returned an error (HTTP {e.code}).")
    except urllib.error.URLError:
        raise HTTPException(503, "Couldn't reach the Gemini API. Check your network connection.")
    # candidates[0].content.parts[*].text — concatenate the text parts.
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError):
        # Most often a safety block or empty candidate; surface a clean message.
        raise HTTPException(502, "Gemini returned no usable text (it may have blocked the response).")


def _call_openai(messages: list[dict], system: str, model: str, key: str) -> str:
    """Ask OpenAI's ChatGPT to explain the circuit, via the Chat Completions REST
    API using only the standard library (urllib) — no extra dependency. The key is
    sent in the Authorization header (never in the URL) and never logged. The persona
    is sent as a leading system message."""
    import json
    import urllib.error
    import urllib.request

    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "max_tokens": 1024,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # 401/403 usually mean a bad key; 404 a bad model name. Don't echo the key/body.
        if e.code in (400, 401, 403):
            raise HTTPException(502, "The AI provider rejected the configured API key or model.")
        raise HTTPException(502, f"The OpenAI API returned an error (HTTP {e.code}).")
    except urllib.error.URLError:
        raise HTTPException(503, "Couldn't reach the OpenAI API. Check your network connection.")
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError):
        raise HTTPException(502, "OpenAI returned no usable text (it may have blocked the response).")


# provider name -> handler. Add new providers here (and to _PROVIDER_CATALOG above);
# the rest of the path is generic.
_AI_PROVIDERS = {
    "anthropic": _call_anthropic,
    "llama": _call_llama,
    "gemini": _call_gemini,
    "openai": _call_openai,
}


# ---- Streaming providers: one generator per provider, yield text chunks -----
# Each handler is a *generator* (yields str chunks). The SSE route wraps them in
# a StreamingResponse. When a provider has no streaming handler, explain_circuit_stream
# falls back to calling the regular handler and yielding the whole text at once.

def _stream_anthropic(messages: list[dict], system: str, model: str, key: str):
    """Yield text chunks from the Anthropic messages streaming API."""
    try:
        import anthropic
    except ImportError:
        raise HTTPException(503, "The 'anthropic' package is not installed (pip install anthropic)")
    client = anthropic.Anthropic(api_key=key)
    try:
        with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
    except anthropic.AuthenticationError:
        raise HTTPException(502, "The AI provider rejected the configured API key.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"The AI provider returned an error (HTTP {e.status_code}).")


def _stream_gemini(messages: list[dict], system: str, model: str, key: str):
    """Yield text chunks from Gemini's streamGenerateContent SSE endpoint."""
    import json as _json
    import urllib.error
    import urllib.request

    contents = [
        {
            "role": ("model" if m["role"] == "assistant" else "user"),
            "parts": [{"text": m["content"]}],
        }
        for m in messages
    ]
    body = _json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
    }).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        ":streamGenerateContent?alt=sse",
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw in resp:
                line = raw.decode("utf-8").rstrip()
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = _json.loads(line[6:])
                    parts = chunk["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts)
                    if text:
                        yield text
                except (KeyError, IndexError, _json.JSONDecodeError):
                    pass
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            raise HTTPException(502, "The AI provider rejected the configured API key or model.")
        raise HTTPException(502, f"The Gemini API returned an error (HTTP {e.code}).")
    except urllib.error.URLError:
        raise HTTPException(503, "Couldn't reach the Gemini API. Check your network connection.")


def _stream_openai(messages: list[dict], system: str, model: str, key: str):
    """Yield text chunks from OpenAI's streaming chat completions endpoint."""
    import json as _json
    import urllib.error
    import urllib.request

    body = _json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "max_tokens": 1024,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw in resp:
                line = raw.decode("utf-8").rstrip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = _json.loads(payload)
                    delta = (chunk["choices"][0]["delta"].get("content") or "")
                    if delta:
                        yield delta
                except (KeyError, IndexError, _json.JSONDecodeError):
                    pass
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            raise HTTPException(502, "The AI provider rejected the configured API key or model.")
        raise HTTPException(502, f"The OpenAI API returned an error (HTTP {e.code}).")
    except urllib.error.URLError:
        raise HTTPException(503, "Couldn't reach the OpenAI API. Check your network connection.")


def _stream_llama(messages: list[dict], system: str, model: str, key: str):
    """Yield text chunks from a local Ollama server with streaming NDJSON."""
    import json as _json
    import urllib.error
    import urllib.request

    body = _json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        f"{AI_LLAMA_HOST}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = _json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
                except _json.JSONDecodeError:
                    pass
    except urllib.error.HTTPError as e:
        detail = (
            "the model isn't available — try `ollama pull %s`" % model
            if e.code == 404
            else f"HTTP {e.code}"
        )
        raise HTTPException(502, f"The local Llama server returned an error ({detail}).")
    except TimeoutError:
        raise HTTPException(
            504,
            f"The local Llama model ({model}) took too long to respond. It may "
            "still be loading — try again in a moment, or use a smaller model.",
        )
    except urllib.error.URLError:
        raise HTTPException(
            503,
            f"Couldn't reach a local Llama server at {AI_LLAMA_HOST}. "
            "Is Ollama running (`ollama serve`)?",
        )


# provider name -> streaming generator handler (yields str chunks).
_AI_STREAM_PROVIDERS = {
    "anthropic": _stream_anthropic,
    "llama": _stream_llama,
    "gemini": _stream_gemini,
    "openai": _stream_openai,
}


def explain_circuit(spec: CircuitSpec, question: str | None = None,
                    history: list[dict] | None = None,
                    persona: str | None = None,
                    provider: str | None = None,
                    model: str | None = None,
                    profile: dict | None = None,
                    onboarding: bool = False) -> str:
    name = provider or default_provider()
    if not name or not provider_ready(name):
        raise HTTPException(403, "That AI provider is not available.")
    handler = _AI_PROVIDERS.get(name)
    if handler is None:
        raise HTTPException(503, f"Unknown AI provider '{name}' (supported: {', '.join(_AI_PROVIDERS)})")
    pconf = PROVIDERS[name]
    use_model = model or pconf["default_model"]
    ground = _persona_grounds(persona)
    # Prior turns first, then the latest circuit summary + question as a new user turn.
    messages = list(history or [])
    messages.append({"role": "user", "content": _build_prompt(spec, question, ground, onboarding)})
    # The learner profile is standing context, so it rides in the system prompt — and
    # only for personas that ground (the unreliable/meep ones don't get it).
    return handler(messages, _system_prompt(persona, profile if ground else None), use_model, pconf["key"])


def explain_circuit_stream(
    spec: CircuitSpec,
    question: str | None = None,
    history: list[dict] | None = None,
    persona: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    profile: dict | None = None,
    onboarding: bool = False,
):
    """Streaming variant of explain_circuit — yields text chunks instead of
    returning the full response.

    For providers that have a streaming handler (all four), text arrives
    token-by-token.  A provider with no entry in _AI_STREAM_PROVIDERS falls
    back to the regular non-streaming handler and yields the whole response
    as a single chunk, so the SSE route still works correctly.
    """
    name = provider or default_provider()
    if not name or not provider_ready(name):
        raise HTTPException(403, "That AI provider is not available.")
    pconf = PROVIDERS[name]
    use_model = model or pconf["default_model"]
    ground = _persona_grounds(persona)
    messages = list(history or [])
    messages.append({"role": "user", "content": _build_prompt(spec, question, ground, onboarding)})
    system = _system_prompt(persona, profile if ground else None)

    stream_handler = _AI_STREAM_PROVIDERS.get(name)
    if stream_handler is None:
        # Fallback: regular handler → single chunk.
        handler = _AI_PROVIDERS.get(name)
        if handler is None:
            raise HTTPException(
                503,
                f"Unknown AI provider '{name}' (supported: {', '.join(_AI_PROVIDERS)})",
            )
        yield handler(messages, system, use_model, pconf["key"])
        return

    yield from stream_handler(messages, system, use_model, pconf["key"])


# ---- Structured-output extraction: free-text intake -> learner profile ------
_EXTRACTION_SYSTEM = (
    "You extract a learner profile from what a student tells a quantum-computing tutor about "
    "themselves. Respond with ONLY a single JSON object — no prose, no markdown fences — with "
    'exactly these string keys: "level", "background", "interests", "goals". '
    '"level" is their self-described experience with quantum computing (e.g. "complete beginner", '
    '"knows the basic gates", "physics grad student"); "background" is their math/physics/'
    'programming background; "interests" is the topics or applications they care about; "goals" '
    "is what they want to learn or achieve. Use an empty string for any field the student didn't "
    "address. Keep each value concise — a phrase or a short sentence — and never invent details "
    "they did not give."
)
_PROFILE_KEYS = ("level", "background", "interests", "goals")


def _parse_json_object(text: str) -> dict:
    """Best-effort parse of a JSON object from a model reply, tolerating prose or
    markdown fences around it. Returns {} if nothing parseable is found."""
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
    return {}


def extract_profile(answer_text: str, provider: str | None = None,
                    model: str | None = None) -> dict:
    """Turn a student's free-text intake answer into a structured profile via the
    configured LLM. Returns {level, background, interests, goals} (empty strings
    where the student said nothing). Robust to models that wrap JSON in prose."""
    name = provider or default_provider()
    if not name or not provider_ready(name):
        raise HTTPException(403, "That AI provider is not available.")
    handler = _AI_PROVIDERS.get(name)
    if handler is None:
        raise HTTPException(503, f"Unknown AI provider '{name}' (supported: {', '.join(_AI_PROVIDERS)})")
    pconf = PROVIDERS[name]
    use_model = model or pconf["default_model"]
    user = f"The student said:\n\n{answer_text}\n\nExtract their profile as a JSON object."
    raw = handler([{"role": "user", "content": user}], _EXTRACTION_SYSTEM, use_model, pconf["key"])
    data = _parse_json_object(raw)
    return {k: str(data.get(k, "") or "").strip() for k in _PROFILE_KEYS}


# ---- Structured-output: retention quiz generation + grading -----------------
# The tutor periodically tests retention by generating a quiz question from the
# recent conversation, then LLM-grades the student's free-text answer.  Both calls
# use the same JSON-only structured-output pattern as extract_profile.

_QUIZ_GENERAL_SYSTEM = (
    "You are a quantum-computing tutor generating a short quiz question to engage a student "
    "who is exploring quantum computing. The student has no recent tutoring context, so pick "
    "any foundational or interesting quantum computing topic — superposition, entanglement, "
    "specific gates (H, CNOT, T, S, Toffoli…), measurement, the Bloch sphere, or algorithms "
    "like Grover's search or Shor's factoring. Aim for a question that builds intuition, "
    "not trivia.\n\n"
    "Respond with ONLY a single JSON object — no prose, no markdown fences.\n\n"
    "Alternate between two question types — roughly half multiple-choice, half open-ended. "
    'Always include: "type" (exactly "text" or "multiple_choice"), "question" (the question '
    'text), "topic" (a one-to-five-word label, e.g. "Hadamard gate"), "expected_answer" '
    "(a thorough reference answer, a few sentences).\n\n"
    'For "multiple_choice" also include: "options" — a JSON array of exactly 4 option '
    "strings (no letter prefixes, just the text), and \"correct_option\" — the letter of "
    'the correct choice: "A", "B", "C", or "D" (corresponding to options[0]–[3]).\n\n'
    "The question must test genuine understanding. The three wrong MC options must be "
    "plausible distractors."
)

_QUIZ_GENERATION_SYSTEM = (
    "You are a quantum-computing tutor generating a short retention-check quiz question "
    "for a student based on what you have just been teaching them. "
    "Respond with ONLY a single JSON object — no prose, no markdown fences.\n\n"
    "Alternate between two question types — roughly half of your questions should be "
    "multiple-choice, the other half open-ended text. Choose whichever fits best for "
    "the concept you're testing; multiple-choice suits factual recall and definitions, "
    "open-ended suits explanations and understanding.\n\n"
    'Always include: "type" (exactly "text" or "multiple_choice"), "question" (the question '
    'text), "topic" (a one-to-five-word label, e.g. "Hadamard gate"), "expected_answer" '
    "(a thorough reference answer, a few sentences).\n\n"
    'For "multiple_choice" also include: "options" — a JSON array of exactly 4 option '
    "strings (no letter prefixes, just the text), and \"correct_option\" — the letter of "
    'the correct choice: "A", "B", "C", or "D" (corresponding to options[0]–[3]).\n\n'
    "The question must test genuine understanding, not guessing from simulator output. "
    "The three wrong MC options must be plausible distractors. "
    "Never repeat a question verbatim from the conversation."
)

_QUIZ_GRADING_SYSTEM = (
    "You are a quantum-computing tutor grading a student's answer to a quiz question. "
    "Respond with ONLY a single JSON object — no prose, no markdown fences — with exactly "
    'these keys: "grade" (string: exactly one of "correct", "partial", or "incorrect"), '
    '"score" (number: 1.0 for correct, 0.5 for partial, 0.0 for incorrect), '
    '"feedback" (string: one concise sentence explaining what was right, wrong, or missing). '
    "Be fair and encouraging. A student who gets the core idea right but misses a detail "
    "is 'partial', not 'incorrect'. Never reveal the expected answer in your feedback "
    "unless the student was incorrect — then you may give a one-sentence hint."
)

# Maximum characters of recent conversation context we send for quiz generation.
_MAX_QUIZ_CONTEXT_CHARS = 4000


_VALID_MC_LETTERS = {"A", "B", "C", "D"}


def generate_quiz(context: str, provider: str | None = None,
                  model: str | None = None,
                  general: bool = False) -> dict:
    """Generate a quiz question from recent tutoring context, or a general quantum
    computing question when ``general=True`` (or when ``context`` is empty).

    Returns a dict with at minimum ``{type, question, topic, expected_answer}``.
    For multiple-choice questions also includes ``{options, correct_option}``.
    On parse failure all string fields are empty (caller should skip the quiz).
    """
    name = provider or default_provider()
    if not name or not provider_ready(name):
        raise HTTPException(403, "That AI provider is not available.")
    handler = _AI_PROVIDERS.get(name)
    if handler is None:
        raise HTTPException(503, f"Unknown AI provider '{name}' (supported: {', '.join(_AI_PROVIDERS)})")
    pconf = PROVIDERS[name]
    use_model = model or pconf["default_model"]
    if general or not context.strip():
        system = _QUIZ_GENERAL_SYSTEM
        user = "Generate a quiz question for a student who is just starting to explore quantum computing."
    else:
        system = _QUIZ_GENERATION_SYSTEM
        trimmed = context.strip()[:_MAX_QUIZ_CONTEXT_CHARS]
        user = (
            "Here is a recent excerpt from a quantum-computing tutoring session:\n\n"
            f"{trimmed}\n\n"
            "Generate a retention quiz question based on something important taught here."
        )
    raw = handler([{"role": "user", "content": user}], system, use_model, pconf["key"])
    data = _parse_json_object(raw)

    quiz_type = str(data.get("type", "") or "").strip().lower()
    if quiz_type not in ("text", "multiple_choice"):
        quiz_type = "text"  # safe default; MC fields simply won't be present

    result: dict = {
        "type": quiz_type,
        "question": str(data.get("question", "") or "").strip(),
        "topic": str(data.get("topic", "") or "").strip(),
        "expected_answer": str(data.get("expected_answer", "") or "").strip(),
        "options": None,
        "correct_option": None,
    }

    if quiz_type == "multiple_choice":
        raw_opts = data.get("options")
        if isinstance(raw_opts, list) and len(raw_opts) == 4:
            result["options"] = [str(o).strip() for o in raw_opts]
        else:
            # Malformed MC falls back to text so we never show a broken MC widget.
            result["type"] = "text"
            result["options"] = None

        co = str(data.get("correct_option", "") or "").strip().upper()
        result["correct_option"] = co if co in _VALID_MC_LETTERS else None
        if result["correct_option"] is None:
            result["type"] = "text"   # can't show MC without a known correct answer

    return result


def grade_mc_quiz(learner_answer: str, correct_option: str,
                  options: list[str] | None = None) -> dict:
    """Grade a multiple-choice answer by direct letter comparison — no LLM call.

    ``learner_answer`` must be a single letter A–D.  Returns
    ``{grade, score, feedback}``."""
    chosen = learner_answer.strip().upper()
    if chosen not in _VALID_MC_LETTERS:
        return {"grade": "incorrect", "score": 0.0,
                "feedback": "Please select one of A, B, C, or D."}
    if chosen == correct_option.upper():
        return {"grade": "correct", "score": 1.0,
                "feedback": "Correct!"}
    # Wrong — build a brief feedback that names the right letter + text if available.
    idx = ord(correct_option.upper()) - ord("A")
    right_text = options[idx] if options and 0 <= idx < len(options) else ""
    feedback = f"Not quite — the correct answer was {correct_option}"
    if right_text:
        feedback += f": {right_text}."
    else:
        feedback += "."
    return {"grade": "incorrect", "score": 0.0, "feedback": feedback}


def grade_quiz(question: str, expected_answer: str, learner_answer: str,
               provider: str | None = None, model: str | None = None) -> dict:
    """LLM-grade a student's open-ended (text) answer against the reference.

    Returns ``{grade, score, feedback}`` where grade is one of
    "correct" / "partial" / "incorrect".  Defaults to incorrect on parse failure
    so a broken grading call doesn't silently pass students.
    Use ``grade_mc_quiz`` for multiple-choice questions instead.
    """
    name = provider or default_provider()
    if not name or not provider_ready(name):
        raise HTTPException(403, "That AI provider is not available.")
    handler = _AI_PROVIDERS.get(name)
    if handler is None:
        raise HTTPException(503, f"Unknown AI provider '{name}' (supported: {', '.join(_AI_PROVIDERS)})")
    pconf = PROVIDERS[name]
    use_model = model or pconf["default_model"]
    user = (
        f"Question: {question}\n\n"
        f"Reference answer: {expected_answer}\n\n"
        f"Student's answer: {learner_answer}\n\n"
        "Grade the student's answer."
    )
    raw = handler([{"role": "user", "content": user}], _QUIZ_GRADING_SYSTEM, use_model, pconf["key"])
    data = _parse_json_object(raw)
    valid_grades = {"correct", "partial", "incorrect"}
    grade = str(data.get("grade", "") or "").strip().lower()
    if grade not in valid_grades:
        grade = "incorrect"
    try:
        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))
    except (TypeError, ValueError):
        score = {"correct": 1.0, "partial": 0.5}.get(grade, 0.0)
    feedback = str(data.get("feedback", "") or "").strip()
    return {"grade": grade, "score": score, "feedback": feedback}


# ---- Persona handoff: farewell + greeting on persona switch -----------------
# When the student switches personas mid-session, both the outgoing and incoming
# professors say something so the transition feels natural and in-character.
# Each message is short (2–4 sentences); the prompt cap keeps cost low.

_HANDOFF_MAX_CHARS = 600  # soft cap — bumped to give room for recap + opinion; hard-trim at 2x

# How many conversation turns to include in the handoff context (user + assistant).
_HANDOFF_HISTORY_TURNS = 10
# How many characters to keep per individual turn (long answers get truncated).
_HANDOFF_TURN_CHARS = 400


def _format_handoff_history(history: list[dict], from_name: str) -> str:
    """Format the tail of a conversation as a compact transcript for the handoff prompt.

    Assistant turns are attributed to ``from_name`` (the outgoing persona).
    Returns an empty string when ``history`` is empty.
    """
    if not history:
        return ""
    tail = history[-_HANDOFF_HISTORY_TURNS:]
    lines = []
    for t in tail:
        speaker = "Student" if t["role"] == "user" else from_name
        snippet = t["content"].replace("\n", " ").strip()[:_HANDOFF_TURN_CHARS]
        lines.append(f"{speaker}: {snippet}")
    return "\n\nRecent conversation:\n" + "\n".join(lines)


def _handoff_call(
    handler,
    pconf: dict,
    model: str,
    actor_voice: str,
    actor_name: str,
    other_name: str,
    action: str,
    context_line: str,
    history_text: str = "",
) -> str:
    """One LLM call for a handoff turn (farewell or greeting).

    ``action`` is ``"farewell"`` or ``"greeting"``; ``context_line`` describes
    the situation; ``history_text`` is a pre-formatted transcript excerpt the
    model can reference. Returns raw stripped text, hard-trimmed at 2 ×
    _HANDOFF_MAX_CHARS so an over-eager model can't blow the budget.
    """
    system = (
        f"{actor_voice}\n\n"
        f"Write a single short {action} message — 2 to 4 sentences, under "
        f"{_HANDOFF_MAX_CHARS} characters. {context_line}"
        f"{history_text}\n\n"
        "Stay completely in character. No preamble, no sign-off label — just "
        "the message itself."
    )
    user = f"[Write the {action} now, in character as {actor_name}.]"
    text = handler([{"role": "user", "content": user}], system, model, pconf["key"])
    return (text or "").strip()[: _HANDOFF_MAX_CHARS * 2]


def persona_handoff(
    from_key: str,
    to_key: str,
    provider: str | None = None,
    model: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    """Generate a farewell from the outgoing persona and a greeting from the
    incoming one, each as a short in-character message.

    ``history`` is the recent conversation (list of ``{"role", "content"}``
    dicts, newest last). When provided, both messages reference what was
    actually discussed: the farewell recaps key topics and shares the outgoing
    persona's impression of the incoming one; the greeting picks up a thread
    and acknowledges the handoff.

    Returns ``{"farewell": str, "greeting": str}``. When the two keys are
    identical (no-op switch) both strings are empty and no LLM call is made.
    Raises 422 for unknown persona keys and 403 when no AI provider is wired.
    """
    if from_key == to_key:
        return {"farewell": "", "greeting": ""}
    if from_key not in PERSONAS:
        raise HTTPException(422, f"unknown persona '{from_key}'")
    if to_key not in PERSONAS:
        raise HTTPException(422, f"unknown persona '{to_key}'")

    name = provider or default_provider()
    if not name or not provider_ready(name):
        raise HTTPException(403, "That AI provider is not available.")
    handler = _AI_PROVIDERS.get(name)
    if handler is None:
        raise HTTPException(
            503,
            f"Unknown AI provider '{name}' (supported: {', '.join(_AI_PROVIDERS)})",
        )
    pconf = PROVIDERS[name]
    use_model = model or pconf["default_model"]

    from_p = PERSONAS[from_key]
    to_p = PERSONAS[to_key]

    history_text = _format_handoff_history(history or [], from_p["name"])

    farewell = _handoff_call(
        handler, pconf, use_model,
        actor_voice=from_p["voice"],
        actor_name=from_p["name"],
        other_name=to_p["name"],
        action="farewell",
        context_line=(
            f"You are {from_p['name']} saying goodbye to a quantum-computing "
            f"student who is switching to {to_p['name']}. Briefly touch on the "
            f"topic(s) you covered together, then hand them off — and feel free "
            f"to share a short, in-character remark about {to_p['name']}."
        ),
        history_text=history_text,
    )
    greeting = _handoff_call(
        handler, pconf, use_model,
        actor_voice=to_p["voice"],
        actor_name=to_p["name"],
        other_name=from_p["name"],
        action="greeting",
        context_line=(
            f"You are {to_p['name']} welcoming a quantum-computing student "
            f"just handed off by {from_p['name']}. Acknowledge the handoff and, "
            f"if there's an interesting thread from their conversation, pick it "
            f"up from your own unique perspective."
        ),
        history_text=history_text,
    )
    return {"farewell": farewell, "greeting": greeting}


# The student can optionally attach a specific question and a prior conversation
# to a circuit. Bounds below keep the prompt (and so the model cost) finite.
MAX_QUESTION_CHARS = 1000
MAX_HISTORY_TURNS = 40        # cap the back-and-forth length
MAX_HISTORY_CHARS = 60000     # and the total transcript size

# How many assistant turns between automatic quiz triggers. Exposed in /config
# so the frontend can match this threshold without hardcoding it.
QUIZ_INTERVAL = int(os.getenv("QCB_QUIZ_INTERVAL", "3"))


def _quantum_target_label() -> str:
    """Short, secret-free label for the configured quantum target (for the UI)."""
    if QUANTUM_PROVIDER == "braket":
        return "Braket local" if BRAKET_DEVICE in _BRAKET_LOCAL_ALIASES else f"Braket {BRAKET_DEVICE.rsplit('/', 1)[-1]}"
    if QUANTUM_PROVIDER == "ibm":
        return IBM_BACKEND
    return QUANTUM_PROVIDER


def _simulated_state(spec: CircuitSpec):
    """Exact local statevector view + per-qubit Bloch vectors for the circuit.

    Returns (statevector_list, bloch, probs). Real hardware can't hand back
    amplitudes or Bloch vectors (measurement only yields counts), so the
    quantum/qsim paths borrow this to populate those views — clearly labeled as a
    simulation, not a measurement."""
    sv = Statevector.from_instruction(build_circuit(spec))
    amps = sv.data
    probs = np.abs(amps) ** 2
    probs = probs / probs.sum()
    n = spec.num_qubits
    statevector = [
        {
            "basis": f"{i:0{n}b}",
            "re": float(a.real),
            "im": float(a.imag),
            "prob": float(p),
            "phase": float(np.degrees(np.angle(a))),
        }
        for i, (a, p) in enumerate(zip(amps, probs))
        if p > 1e-9
    ]
    bloch = [bloch_vector(sv, q, n) for q in range(n)]
    return statevector, bloch, probs


# ---- Export: Qiskit source + OpenQASM 3 ------------------------------------
# Turn the validated spec into copy-pasteable code the user can run elsewhere.
# Both representations describe the same circuit and end with a measurement of
# every qubit, so they reproduce the histogram the playground shows.
def _fmt_angle(rad: float) -> str:
    """Render a rotation angle as a clean multiple of pi when it is one (e.g.
    'pi/2', '3*pi/4'), else a plain float. Keeps the generated code readable for
    the common preset angles without ever losing precision for arbitrary ones."""
    from fractions import Fraction

    if rad == 0:
        return "0"
    ratio = rad / math.pi
    frac = Fraction(ratio).limit_denominator(360)
    if abs(float(frac) - ratio) < 1e-9:
        num, den = frac.numerator, frac.denominator
        sign = "-" if num < 0 else ""
        num = abs(num)
        body = "pi" if num == 1 else f"{num}*pi"
        return f"{sign}{body}" if den == 1 else f"{sign}{body}/{den}"
    return repr(round(rad, 6))


def _qiskit_source(spec: CircuitSpec) -> str:
    """Generate runnable Qiskit Python that rebuilds this exact circuit. Method
    names are the whitelisted gate names — the same ones build_circuit dispatches
    to — so the source mirrors what the backend actually ran."""
    needs_pi = any(g.param is not None for g in spec.gates)
    lines = ["from qiskit import QuantumCircuit"]
    if needs_pi:
        lines.append("from numpy import pi")
    lines += ["", f"qc = QuantumCircuit({spec.num_qubits})"]
    for g in spec.gates:
        _, takes_param = ALLOWED[g.name]
        qubits = ", ".join(str(q) for q in g.qubits)
        if takes_param:
            lines.append(f"qc.{g.name}({_fmt_angle(g.param)}, {qubits})")
        else:
            lines.append(f"qc.{g.name}({qubits})")
    lines.append("qc.measure_all()")
    return "\n".join(lines)


