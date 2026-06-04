"""FastAPI backend: builds a Qiskit circuit from JSON and returns simulation results."""

import math
import os
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from qiskit import QuantumCircuit
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
MAX_QUBITS = int(os.getenv("QCB_MAX_QUBITS", "12"))
MAX_GATES = int(os.getenv("QCB_MAX_GATES", "2000"))
MAX_SHOTS = int(os.getenv("QCB_MAX_SHOTS", "100000"))


def _envflag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Real-quantum-hardware feature flag + IBM Quantum credentials (all optional).
# When QCB_ENABLE_QUANTUM_HW is off, the /config endpoint hides the UI toggle and
# any quantum-mode request is rejected, so none of the IBM code path is reachable.
ENABLE_QUANTUM_HW = _envflag("QCB_ENABLE_QUANTUM_HW")
IBM_TOKEN = os.getenv("QCB_IBM_TOKEN", "").strip()
IBM_INSTANCE = os.getenv("QCB_IBM_INSTANCE", "").strip()
IBM_CHANNEL = os.getenv("QCB_IBM_CHANNEL", "ibm_quantum_platform").strip()
# "aer" -> local Aer simulator (no credentials, instant; mimics the hardware
# counts-only path). "least_busy" -> least-busy real device. Anything else is
# treated as a specific IBM backend name (e.g. "ibm_brisbane").
IBM_BACKEND = os.getenv("QCB_IBM_BACKEND", "aer").strip()

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

app = FastAPI(title="Quantum Circuit Playground")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class Gate(BaseModel):
    name: str
    qubits: list[int]
    param: float | None = None


class CircuitSpec(BaseModel):
    num_qubits: int
    shots: int = 1024
    gates: list[Gate] = []
    # "sim"     -> local statevector simulation (exact amplitudes; default).
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
    """Run the circuit and return (counts, backend_label). Hardware/Aer give
    measurement counts only — there is no readable statevector or Bloch vector.

    force_aer pins the run to the local Aer simulator (used by the 'qsim' mode),
    ignoring QCB_IBM_BACKEND."""
    qc = build_circuit(spec)
    qc.measure_all()

    backend_choice = "aer" if force_aer else IBM_BACKEND
    if backend_choice in ("aer", "local"):
        try:
            from qiskit_aer import AerSimulator
        except ImportError:
            raise HTTPException(503, "qiskit-aer is not installed (pip install qiskit-aer)")

        # Our whitelisted gates are all natively supported by Aer, so we run the
        # circuit directly. Transpiling to Aer's full target costs ~4s per call
        # (pointless here) — skipping it makes a roll feel instant.
        sim = AerSimulator()
        result = sim.run(qc, shots=spec.shots).result()
        counts = result.get_counts()
        label = "aer_simulator (local)"
    else:
        from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
        from qiskit_ibm_runtime import SamplerV2

        service = _get_service()
        if IBM_BACKEND == "least_busy":
            backend = service.least_busy(operational=True, simulator=False)
        else:
            backend = service.backend(IBM_BACKEND)
        isa = generate_preset_pass_manager(optimization_level=1, backend=backend).run(qc)
        job = SamplerV2(mode=backend).run([isa], shots=spec.shots)
        counts = job.result()[0].data.meas.get_counts()
        label = backend.name

    # Normalize bitstring keys (drop any register spacing) to match the sim path.
    counts = {k.replace(" ", ""): int(v) for k, v in counts.items()}
    return counts, label


@app.get("/config")
def config():
    """Tell the frontend whether the quantum-hardware toggle should appear."""
    return {"quantum_enabled": ENABLE_QUANTUM_HW, "backend": IBM_BACKEND if ENABLE_QUANTUM_HW else None}


@app.post("/simulate")
def simulate(spec: CircuitSpec):
    validate(spec)
    n = spec.num_qubits

    if spec.mode in ("qsim", "quantum"):
        counts, label = run_quantum(spec, force_aer=(spec.mode == "qsim"))
        return {"counts": counts, "statevector": [], "bloch": [], "backend": label, "mode": spec.mode}

    sv = Statevector.from_instruction(build_circuit(spec))
    amps = sv.data
    probs = np.abs(amps) ** 2
    probs = probs / probs.sum()

    sampled = np.random.multinomial(spec.shots, probs)
    counts = {f"{i:0{n}b}": int(c) for i, c in enumerate(sampled) if c > 0}

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
    return {
        "counts": counts,
        "statevector": statevector,
        "bloch": bloch,
        "backend": "statevector (local)",
        "mode": "sim",
    }


# Serve the static frontend at the root (registered last so /simulate wins).
app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
