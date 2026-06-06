"""Curated quantum-computing reference notes + retrieval for the AI explainer (RAG).

The explainer already grounds the model in the *specific circuit*: `_circuit_summary`
in core.py hands it the gate list, the OpenQASM 3 source, and the exact statevector
outcome probabilities. This module adds the missing half — grounding in the *quantum
concepts* — and serves two retrieval paths:

1. Circuit grounding (structured lookup). Every gate the user can place is in
   core.ALLOWED, so the "query" (which gates are present) is already structured: we map
   gate name -> note directly (GATE_NOTES/CONCEPT_NOTES via `retrieve`/`reference_block`).
   Precise, instant, free, and — because the corpus is hand-authored — truthful by
   construction.

2. Free-text grounding (TF-IDF retrieval). Tutor mode also accepts questions that
   aren't tied to a placed gate ("how does Grover's algorithm work?"). TOPIC_NOTES
   (algorithms, concepts, hardware) is searched by an offline NumPy TF-IDF retriever
   (`retrieve_topics`) — no embeddings, no network — tuned and regression-tested
   against EVAL_CASES. `combined_reference_block` merges both corpora for the prompt.

Keeping it truthful (see CLAUDE.md): when you add a gate to core.ALLOWED, add its
note to GATE_NOTES here in the *same* change. The test suite asserts every
whitelisted gate has a note, so a missing one fails CI rather than silently shipping.
"""
import re
from dataclasses import dataclass, field

import numpy as np

# Per-gate notes, keyed by the lowercase backend method name (the same key used in
# core.ALLOWED and emitted by _circuit_summary). Each value is (title, text). The
# text states what the gate does and, for multi-qubit gates, the qubit-order
# convention — the gate list gives qubit indices in exactly this order.
GATE_NOTES = {
    "h": (
        "Hadamard (H)",
        "Single-qubit basis-change gate. It maps |0> to (|0>+|1>)/sqrt(2) and |1> to "
        "(|0>-|1>)/sqrt(2), creating an equal superposition with a relative phase. It "
        "is its own inverse (H applied twice is the identity) and converts between the "
        "Z (computational) and X bases.",
    ),
    "x": (
        "Pauli-X (NOT)",
        "Single-qubit bit flip: it swaps |0> and |1>. Geometrically it is a 180-degree "
        "rotation of the Bloch vector about the X axis. It is the quantum NOT gate.",
    ),
    "y": (
        "Pauli-Y",
        "Single-qubit gate that both flips the bit and adds a phase: |0> -> i|1> and "
        "|1> -> -i|0>. It is a 180-degree rotation about the Y axis of the Bloch sphere.",
    ),
    "z": (
        "Pauli-Z (phase flip)",
        "Single-qubit phase flip: it leaves |0> unchanged and sends |1> to -|1>. On its "
        "own it never changes measurement probabilities in the computational basis; it "
        "acts only on phase, and so matters through later interference.",
    ),
    "s": (
        "S (phase, sqrt(Z))",
        "Single-qubit quarter-turn phase gate: it leaves |0> alone and sends |1> to "
        "i|1> (a 90-degree rotation about Z). Applying S twice gives Z.",
    ),
    "t": (
        "T (pi/8 gate)",
        "Single-qubit eighth-turn phase gate: it leaves |0> alone and sends |1> to "
        "e^{i*pi/4}|1> (a 45-degree rotation about Z). Applying T twice gives S.",
    ),
    "sdg": (
        "S-dagger (inverse S)",
        "The inverse of the S gate: it leaves |0> alone and sends |1> to -i|1>, "
        "undoing S's quarter-turn phase about Z.",
    ),
    "tdg": (
        "T-dagger (inverse T)",
        "The inverse of the T gate: it leaves |0> alone and sends |1> to "
        "e^{-i*pi/4}|1>, undoing T's eighth-turn phase about Z.",
    ),
    "rx": (
        "RX(theta) rotation",
        "Single-qubit rotation by angle theta about the X axis of the Bloch sphere. "
        "RX(pi) equals the X gate up to a global phase; small angles tilt the state "
        "partway, creating a tunable superposition.",
    ),
    "ry": (
        "RY(theta) rotation",
        "Single-qubit rotation by angle theta about the Y axis. It keeps amplitudes "
        "real, so RY(pi/2) on |0> gives the equal superposition (|0>+|1>)/sqrt(2) with "
        "no relative phase; theta tunes the |0> vs |1> probabilities continuously.",
    ),
    "rz": (
        "RZ(theta) rotation",
        "Single-qubit rotation by angle theta about the Z axis: it adds a relative "
        "phase between |0> and |1>. Like Z, it does not change computational-basis "
        "probabilities by itself and acts only through interference.",
    ),
    "cx": (
        "CX (CNOT)",
        "Two-qubit controlled-NOT and the primary entangling gate. Qubit order is "
        "[control, target]: it flips the target if and only if the control is |1>. "
        "Applied to a control in superposition (e.g. after H) it creates entanglement, "
        "as in the Bell state.",
    ),
    "cz": (
        "CZ (controlled-Z)",
        "Two-qubit controlled phase flip: it multiplies the |11> component by -1 and "
        "leaves the others unchanged. It is symmetric in its two qubits (control and "
        "target are interchangeable) and can entangle qubits that are in superposition.",
    ),
    "swap": (
        "SWAP",
        "Two-qubit gate that exchanges the states of its two qubits. On its own it does "
        "not create entanglement from a product state; it just relabels which qubit "
        "holds which state.",
    ),
    "cp": (
        "CP(theta) (controlled phase)",
        "Two-qubit controlled-phase gate: it multiplies the |11> component by "
        "e^{i*theta} and leaves the others unchanged. It is symmetric in its two "
        "qubits; theta = pi reproduces CZ. It is a building block of the quantum "
        "Fourier transform.",
    ),
    "ccx": (
        "CCX (Toffoli)",
        "Three-qubit controlled-controlled-NOT. Qubit order is [control1, control2, "
        "target]: it flips the target if and only if both controls are |1>. It is a "
        "reversible AND gate and is universal for classical logic.",
    ),
    "cswap": (
        "CSWAP (Fredkin)",
        "Three-qubit controlled-SWAP. Qubit order is [control, target1, target2]: it "
        "swaps the two targets if and only if the control is |1>.",
    ),
}

# Foundational concept notes, keyed by a short tag. These are not tied to one gate;
# they frame how to read the circuit and its numbers.
CONCEPT_NOTES = {
    "endianness": (
        "Qubit ordering (little-endian)",
        "Qiskit is little-endian: in every basis string and every measurement-count "
        "key, the rightmost bit is qubit 0 and the leftmost is the highest-numbered "
        "qubit. Always state explicitly which bit corresponds to which qubit.",
    ),
    "measurement": (
        "Measurement and probabilities",
        "Measuring in the computational (Z) basis collapses the state; the probability "
        "of each outcome is the squared magnitude of its amplitude. Many shots only "
        "estimate this distribution. A global phase is unobservable, and relative phases "
        "show up only after they are turned into amplitude differences by interference.",
    ),
    "superposition": (
        "Superposition",
        "A qubit can be a linear combination alpha|0> + beta|1>, with measurement "
        "probabilities |alpha|^2 and |beta|^2. Gates such as H and RY create "
        "superposition; it is what lets a circuit explore many basis states at once.",
    ),
    "entanglement": (
        "Entanglement",
        "An entangled multi-qubit state cannot be written as an independent state per "
        "qubit. It is typically made by putting a qubit in superposition and then "
        "applying a controlled gate (H then CX gives a Bell pair). Measuring one qubit "
        "then constrains the correlated outcomes of the others.",
    ),
    "interference": (
        "Phase and interference",
        "Phase gates (Z, S, T, their inverses, RZ, CP) add relative phases that are "
        "invisible to measurement on their own. They matter when a later gate (often H) "
        "recombines amplitudes so they add or cancel — constructive and destructive "
        "interference is how quantum algorithms concentrate probability on the answers "
        "they want.",
    ),
}

# Concept notes that are always relevant: they prevent the single most common
# misreading (bit order) and explain what the probabilities mean.
_ALWAYS_CONCEPTS = ("endianness", "measurement")

# Which extra concepts a gate's presence implies. Listed in priority order; dedup
# preserves first-seen order so the most foundational ideas come first.
_GATE_CONCEPTS = {
    "h": ("superposition",),
    "rx": ("superposition",),
    "ry": ("superposition",),
    "z": ("interference",),
    "s": ("interference",),
    "t": ("interference",),
    "sdg": ("interference",),
    "tdg": ("interference",),
    "rz": ("interference",),
    "cx": ("entanglement",),
    "cz": ("entanglement", "interference"),
    "cp": ("entanglement", "interference"),
    "ccx": ("entanglement",),
    "cswap": ("entanglement",),
}

# Bounds, mirroring the spirit of the other prompt caps in core.py: a curated
# corpus is small, but a pathological circuit could touch every gate, so cap the
# injected reference by both chunk count and total characters.
MAX_CHUNKS = 16
MAX_CHARS = 4500


def retrieve(spec):
    """Return the ordered list of (title, text) reference chunks for this circuit.

    Gate notes come first, in the order the gates first appear (deduped), then the
    foundational concept notes (always-on plus any implied by the gates present),
    also deduped and in a stable order. The result is bounded by MAX_CHUNKS and
    MAX_CHARS so the injected context stays small regardless of the circuit.
    """
    names = []
    for g in spec.gates:
        if g.name not in names:
            names.append(g.name)

    chunks = []
    for name in names:
        note = GATE_NOTES.get(name)  # unknown names are skipped (validate() rejects them upstream)
        if note:
            chunks.append(note)

    concepts = list(_ALWAYS_CONCEPTS)
    for name in names:
        for c in _GATE_CONCEPTS.get(name, ()):
            if c not in concepts:
                concepts.append(c)
    for c in concepts:
        chunks.append(CONCEPT_NOTES[c])

    return _bounded(chunks)


def _bounded(chunks):
    """Trim to at most MAX_CHUNKS notes and roughly MAX_CHARS total characters,
    keeping earlier (higher-priority) chunks. Always returns at least one chunk if
    any were offered, so a single oversized note can't blank the whole block."""
    out = []
    total = 0
    for title, text in chunks:
        if len(out) >= MAX_CHUNKS:
            break
        size = len(title) + len(text)
        if out and total + size > MAX_CHARS:
            break
        out.append((title, text))
        total += size
    return out


_BLOCK_HEADER = (
    "Reference notes (authoritative background; rely on them to stay correct, "
    "but explain in your own words rather than quoting them):"
)


def _render_block(chunks):
    """Render (title, text) chunks as the prompt's reference block, or "" if empty."""
    if not chunks:
        return ""
    return "\n".join([_BLOCK_HEADER] + [f"- {title}: {text}" for title, text in chunks])


def reference_block(spec):
    """Render the gate/concept notes for a circuit as a plain-text reference block,
    or "" if there is nothing to add. The model is told to use it for correctness
    but not to quote it verbatim, so it informs the explanation without leaking
    into the persona's voice."""
    return _render_block(retrieve(spec))


# =========================================================================== #
# Free-text topic corpus.
#
# The gate/concept notes above are retrieved by *structured* lookup: the circuit
# tells us exactly which gates are present. But tutor mode lets the student ask
# free-text questions ("how does Grover's algorithm work?", "what is decoherence?")
# that aren't tied to any placed gate. Those need *semantic* retrieval over a
# broader body of notes — algorithms, concepts, and hardware.
#
# This section defines that corpus + an evaluation set; the TF-IDF retriever that
# scores a free-text query against it is defined further down (`retrieve_topics`) and
# is measured against EVAL_CASES in the test suite.
#
# Truthfulness rule (CLAUDE.md) applies here too: every note is hand-authored,
# paraphrased standard textbook material — no long verbatim quotes. Keep each note
# short and correct; the integrity tests assert the corpus stays well-formed and
# that every eval case points at a note that actually exists.
# =========================================================================== #

# Categories let the retriever route/merge by kind (e.g. prefer a hardware note for
# a hardware question). Keep this tuple and every note's `category` in sync.
TOPIC_CATEGORIES = ("algorithm", "concept", "hardware")


@dataclass(frozen=True)
class TopicNote:
    """One free-text reference note. `id` is a stable slug (used by the eval set and,
    later, citations); `keywords` are extra surface forms — synonyms, abbreviations,
    alternate spellings — that the retriever can match against beyond the title/text."""
    id: str
    title: str
    category: str
    text: str
    keywords: tuple[str, ...] = field(default_factory=tuple)


def _note(id, title, category, text, keywords=()):  # terse constructor for the table
    return TopicNote(id=id, title=title, category=category, text=text,
                     keywords=tuple(keywords))


# The corpus, keyed by id. Notes are 2-4 sentences: enough to ground an answer,
# short enough to inject several without blowing the prompt budget.
TOPIC_NOTES = {n.id: n for n in (
    # ---- Algorithms & protocols ------------------------------------------- #
    _note(
        "bell_state", "Bell state (EPR pair)", "algorithm",
        "The simplest entangled state of two qubits, e.g. (|00>+|11>)/sqrt(2). It is "
        "built by putting one qubit in superposition with H and then applying CX with "
        "that qubit as control. Measuring either qubit instantly fixes the other's "
        "outcome, yet neither qubit has a definite value on its own.",
        ("bell state", "epr pair", "epr", "bell pair", "entangled pair"),
    ),
    _note(
        "ghz_state", "GHZ state", "algorithm",
        "A maximally entangled state of three or more qubits, (|00..0>+|11..1>)/sqrt(2). "
        "It is made with one H followed by a chain of CX gates fanning out from the "
        "first qubit. All qubits are correlated: a measurement of one determines the "
        "rest. GHZ states sharply expose the non-classical nature of entanglement.",
        ("greenberger horne zeilinger", "cat state", "three qubit entanglement"),
    ),
    _note(
        "deutsch_jozsa", "Deutsch-Jozsa algorithm", "algorithm",
        "An early algorithm that decides whether a black-box function is constant or "
        "balanced with a single query, where a classical approach may need exponentially "
        "many. It works by querying all inputs in superposition and using interference so "
        "the answer shows up deterministically in one measurement. Mainly of conceptual "
        "value: it was a first clear separation between quantum and classical query cost.",
        ("deutsch jozsa", "constant or balanced", "oracle algorithm"),
    ),
    _note(
        "bernstein_vazirani", "Bernstein-Vazirani algorithm", "algorithm",
        "Finds a hidden bit-string s encoded in a function f(x) = s.x (mod 2) with one "
        "quantum query, versus n classical queries. It puts the input register in a "
        "uniform superposition with Hadamards, applies the oracle as a phase, and a "
        "second layer of Hadamards interferes the phases so the register reads out s "
        "directly.",
        ("bernstein vazirani", "hidden string", "phase kickback"),
    ),
    _note(
        "grover", "Grover's search algorithm", "algorithm",
        "Searches an unstructured space of N items in about sqrt(N) steps, a quadratic "
        "speedup over classical O(N). It repeats two operations -- an oracle that flips "
        "the phase of the target item and a diffusion operator that reflects amplitudes "
        "about their mean -- to steadily amplify the target's amplitude (amplitude "
        "amplification). Too many iterations overshoot and reduce the success probability.",
        ("grover search", "amplitude amplification", "unstructured search", "diffusion operator"),
    ),
    _note(
        "shor", "Shor's factoring algorithm", "algorithm",
        "Factors large integers in polynomial time, threatening RSA-style cryptography. "
        "It reduces factoring to finding the period of a modular-exponentiation function "
        "and finds that period with the quantum Fourier transform via phase estimation. "
        "The exponential speedup comes from the period-finding step, not from trying all "
        "factors in parallel.",
        ("shor factoring", "period finding", "rsa breaking", "integer factorization"),
    ),
    _note(
        "qft", "Quantum Fourier transform (QFT)", "algorithm",
        "The quantum analogue of the discrete Fourier transform: it maps amplitudes to "
        "their frequency representation across the computational basis. It is built from "
        "Hadamards and controlled-phase (CP) rotations and runs in O(n^2) gates on n "
        "qubits, exponentially fewer than the classical FFT's O(n 2^n). It is the engine "
        "behind phase estimation and Shor's algorithm.",
        ("quantum fourier transform", "qft", "fourier"),
    ),
    _note(
        "phase_estimation", "Quantum phase estimation (QPE)", "algorithm",
        "Estimates the eigenphase theta of a unitary U for an eigenstate |psi>, i.e. the "
        "phase in U|psi> = e^{2 pi i theta}|psi>. Controlled powers of U kick the phase "
        "onto a register of counting qubits, and an inverse QFT reads theta out in binary. "
        "It is a core subroutine of Shor's algorithm and many quantum-chemistry methods.",
        ("phase estimation", "qpe", "eigenphase", "eigenvalue estimation"),
    ),
    _note(
        "teleportation", "Quantum teleportation", "algorithm",
        "Transfers an unknown qubit state from sender to receiver using a shared Bell "
        "pair plus two classical bits -- the qubit itself never travels. The sender does "
        "a Bell-basis measurement and sends the two-bit result; the receiver applies X "
        "and/or Z corrections to recover the state. It does not beat the speed of light "
        "(the classical bits must be sent) and the original state is destroyed, consistent "
        "with no-cloning.",
        ("teleport", "quantum teleportation", "state transfer"),
    ),
    _note(
        "superdense_coding", "Superdense coding", "algorithm",
        "The dual of teleportation: sending two classical bits by transmitting a single "
        "qubit, given a pre-shared Bell pair. The sender encodes the two bits with one of "
        "{I, X, Z, ZX} (the last equals Y up to a phase) on their half and sends it; the "
        "receiver undoes the entangling "
        "circuit and measures both bits. It shows entanglement can double the classical "
        "capacity of one qubit.",
        ("superdense", "dense coding", "two bits one qubit"),
    ),
    _note(
        "vqe", "Variational quantum eigensolver (VQE)", "algorithm",
        "A hybrid quantum-classical method for estimating the ground-state energy of a "
        "molecule or Hamiltonian. A parameterized circuit (ansatz) prepares a trial state, "
        "the quantum device measures its energy, and a classical optimizer adjusts the "
        "parameters to minimize that energy. It is designed for noisy near-term (NISQ) "
        "hardware because the circuits stay shallow.",
        ("vqe", "variational eigensolver", "ground state energy", "ansatz"),
    ),
    _note(
        "qaoa", "Quantum approximate optimization algorithm (QAOA)", "algorithm",
        "A hybrid algorithm for combinatorial optimization (e.g. Max-Cut). It alternates "
        "a problem (cost) Hamiltonian and a mixing Hamiltonian for p layers, with angles "
        "tuned by a classical optimizer to bias measurements toward good solutions. Like "
        "VQE it is variational and aimed at NISQ devices; higher p can improve quality at "
        "the cost of depth.",
        ("qaoa", "approximate optimization", "max cut", "combinatorial optimization"),
    ),
    # ---- Concepts --------------------------------------------------------- #
    _note(
        "bloch_sphere", "Bloch sphere", "concept",
        "A geometric picture of a single qubit's pure state as a point on the surface of "
        "a unit sphere. The north and south poles are |0> and |1>; the equator holds equal "
        "superpositions differing by phase. Single-qubit gates are rotations of this "
        "sphere, and mixed (noisy) states sit inside it rather than on the surface.",
        ("bloch", "bloch vector", "qubit geometry"),
    ),
    _note(
        "no_cloning", "No-cloning theorem", "concept",
        "An unknown quantum state cannot be copied exactly. There is no unitary that turns "
        "|psi>|0> into |psi>|psi> for every |psi> -- linearity forbids it. This is why "
        "teleportation destroys the original, why you can't back up a qubit, and why "
        "eavesdropping on quantum key distribution is detectable.",
        ("no cloning", "cannot copy qubit", "cloning theorem"),
    ),
    _note(
        "universal_gates", "Universal gate sets", "concept",
        "A gate set is universal if it can approximate any unitary to arbitrary accuracy. "
        "A common example is {H, T, CNOT}: the single-qubit gates generate any rotation "
        "and CNOT supplies entanglement. The Solovay-Kitaev theorem guarantees efficient "
        "approximation, so a small discrete set suffices for any algorithm.",
        ("universal gate set", "solovay kitaev", "clifford t"),
    ),
    _note(
        "global_vs_relative_phase", "Global vs relative phase", "concept",
        "A global phase multiplying the whole state (e.g. e^{i*phi}|psi>) is physically "
        "unobservable -- it never affects any measurement. A relative phase between basis "
        "components (e.g. |0> + e^{i*phi}|1>) is real and observable: it changes outcomes "
        "once a later gate, often H, converts it into amplitude differences via "
        "interference.",
        ("global phase", "relative phase", "unobservable phase"),
    ),
    _note(
        "decoherence", "Decoherence", "concept",
        "The loss of a qubit's quantum behavior as it interacts with its environment, "
        "turning fragile superpositions and entanglement into ordinary classical "
        "uncertainty. It is the main obstacle to large quantum computers and is "
        "characterized by the T1 (energy relaxation) and T2 (dephasing) times. Error "
        "correction and fast gates are the defenses against it.",
        ("decoherence", "dephasing", "loss of coherence", "environment noise"),
    ),
    _note(
        "density_matrix", "Mixed states and the density matrix", "concept",
        "When a qubit is noisy or entangled with something we ignore, it isn't described "
        "by a single state vector but by a density matrix rho -- a statistical mixture of "
        "pure states. Pure states satisfy tr(rho^2) = 1; mixed states have tr(rho^2) < 1. "
        "Tracing out part of an entangled system leaves the rest in a mixed state.",
        ("density matrix", "mixed state", "rho", "partial trace"),
    ),
    _note(
        "fidelity", "Fidelity", "concept",
        "A measure (0 to 1) of how close two quantum states, or an achieved operation and "
        "its ideal, are -- 1 means identical. Gate and state fidelities are the standard "
        "way to report hardware quality; 'two-qubit gate fidelity' is often the limiting "
        "metric for a device. It is closely tied to error rate (error ~ 1 - fidelity).",
        ("fidelity", "gate fidelity", "state fidelity", "error rate"),
    ),
    _note(
        "quantum_parallelism", "Quantum parallelism (and its limits)", "concept",
        "Putting an input register in superposition lets a circuit evaluate a function on "
        "all inputs 'at once', but measurement returns just one random outcome -- so you "
        "cannot simply read off every answer. Useful algorithms (Grover, Shor) instead use "
        "interference to make the wanted answers reinforce and the rest cancel. The speedup "
        "comes from interference, not from brute-force parallel readout.",
        ("quantum parallelism", "try all at once", "evaluate all inputs"),
    ),
    # ---- Hardware --------------------------------------------------------- #
    _note(
        "nisq", "NISQ era", "hardware",
        "NISQ -- Noisy Intermediate-Scale Quantum -- describes today's devices: tens to a "
        "few hundred qubits, no full error correction, and gate errors that limit circuit "
        "depth. Algorithms that suit NISQ hardware (VQE, QAOA) keep circuits shallow and "
        "lean on classical co-processing. The term was coined by John Preskill.",
        ("nisq", "noisy intermediate scale", "near term quantum"),
    ),
    _note(
        "superconducting", "Superconducting qubits", "hardware",
        "Qubits made from superconducting circuits (e.g. transmons) cooled to "
        "near absolute zero, with states controlled by microwave pulses. They switch fast "
        "(nanosecond gates) but have relatively short coherence and usually only "
        "nearest-neighbor connectivity. Used by IBM and Google.",
        ("superconducting qubit", "transmon", "ibm google qubit", "microwave"),
    ),
    _note(
        "trapped_ions", "Trapped-ion qubits", "hardware",
        "Qubits encoded in the electronic states of individual ions held in "
        "electromagnetic traps and manipulated with lasers. They offer long coherence "
        "times and all-to-all connectivity with very high gate fidelity, but gates are "
        "slower than in superconducting systems. Used by IonQ and Quantinuum.",
        ("trapped ion", "ion trap", "ionq", "quantinuum", "laser qubit"),
    ),
    _note(
        "error_correction", "Quantum error correction (QEC)", "hardware",
        "Protects quantum information by spreading one logical qubit across many physical "
        "qubits, so errors can be detected and corrected without measuring (and collapsing) "
        "the data itself. It relies on measuring stabilizers/syndromes rather than the "
        "qubits' values. Fault-tolerant computing needs error rates below a threshold and a "
        "large physical-to-logical qubit overhead.",
        ("error correction", "qec", "logical qubit", "stabilizer", "syndrome"),
    ),
    _note(
        "surface_code", "Surface code", "hardware",
        "A leading quantum error-correcting code that arranges physical qubits on a 2D grid "
        "and needs only nearest-neighbor measurements, which suits planar superconducting "
        "chips. It has a comparatively high error threshold (around 1%) but a large qubit "
        "overhead per logical qubit. It is the front-runner for fault-tolerant hardware.",
        ("surface code", "toric code", "2d lattice code"),
    ),
    _note(
        "t1_t2", "Coherence times (T1 and T2)", "hardware",
        "T1 is the energy-relaxation time -- how long a qubit stays in |1> before decaying "
        "to |0>. T2 is the dephasing time -- how long a superposition keeps a well-defined "
        "relative phase -- and is at most 2*T1. Longer T1/T2 mean more gates can run before "
        "decoherence corrupts the result, so they are headline hardware metrics.",
        ("t1", "t2", "coherence time", "relaxation time", "dephasing time"),
    ),
    _note(
        "connectivity", "Qubit connectivity (coupling map)", "hardware",
        "The coupling map says which qubit pairs can directly run a two-qubit gate. Limited "
        "connectivity (e.g. nearest-neighbor on a superconducting chip) forces extra SWAP "
        "gates to bring distant qubits together, adding depth and error. All-to-all "
        "connectivity (typical of trapped ions) avoids this routing overhead.",
        ("connectivity", "coupling map", "qubit topology", "swap routing", "nearest neighbor"),
    ),
)}


# Evaluation set for the retriever: each case is a free-text query and the set of
# note ids a good retriever should surface near the top. The retriever's recall@k is
# measured against these in the test suite; the integrity tests also assert every
# expected id exists, so the eval set can't silently reference a deleted note.
EVAL_CASES = (
    ("how does grover's algorithm work", {"grover"}),
    ("what is amplitude amplification", {"grover"}),
    ("what is a bell state", {"bell_state"}),
    ("explain an epr pair", {"bell_state"}),
    ("what is a ghz state", {"ghz_state"}),
    ("how does quantum teleportation work", {"teleportation"}),
    ("what is superdense coding", {"superdense_coding"}),
    ("how does shor's algorithm factor numbers", {"shor"}),
    ("what is the quantum fourier transform", {"qft"}),
    ("what is quantum phase estimation", {"phase_estimation"}),
    ("explain the deutsch jozsa algorithm", {"deutsch_jozsa"}),
    ("what is the bernstein vazirani algorithm", {"bernstein_vazirani"}),
    ("what is vqe", {"vqe"}),
    ("what is qaoa", {"qaoa"}),
    ("what is the bloch sphere", {"bloch_sphere"}),
    ("explain the no cloning theorem", {"no_cloning"}),
    ("what does a universal gate set mean", {"universal_gates"}),
    ("difference between global and relative phase", {"global_vs_relative_phase"}),
    ("what is decoherence", {"decoherence"}),
    ("what is a density matrix or mixed state", {"density_matrix"}),
    ("what is gate fidelity", {"fidelity"}),
    ("can a quantum computer try all answers at once", {"quantum_parallelism"}),
    ("what is the nisq era", {"nisq"}),
    ("how do superconducting qubits work", {"superconducting"}),
    ("how do trapped ion quantum computers work", {"trapped_ions"}),
    ("what is quantum error correction", {"error_correction"}),
    ("what is the surface code", {"surface_code"}),
    ("what are t1 and t2 coherence times", {"t1_t2"}),
    ("what is qubit connectivity and a coupling map", {"connectivity"}),
)


# =========================================================================== #
# TF-IDF retriever over the topic corpus.
#
# A small, offline, dependency-free (NumPy only) semantic search for the free-text
# tutor questions. The corpus is ~30 short notes, so we build the whole TF-IDF index
# once at import — no service, no embeddings, no network. The scoring blends three
# signals (cosine similarity + IDF-weighted term overlap is the base; an exact
# keyword/alias phrase match adds a fusion boost), abstains when nothing clears a
# confidence floor, and uses MMR so two near-duplicate notes don't crowd out variety.
# =========================================================================== #

# Retriever knobs. Defaults are tuned against EVAL_CASES (see the eval test).
TOPIC_TOP_K = 3          # how many notes a query may pull in
MIN_SCORE = 0.06         # fused score floor below which we abstain (return nothing)
KEYWORD_BOOST = 0.35     # additive bonus when a note's keyword/alias phrase is in the query
KEYWORD_DOC_WEIGHT = 2   # how many times keyword tokens are folded into a note's document
MMR_LAMBDA = 0.7         # MMR tradeoff: 1.0 = pure relevance, 0.0 = pure diversity

# A compact English/quantum-question stoplist: function words plus the filler that
# shows up in nearly every question ("what is...", "how does...") and so carries no
# discriminating signal. Kept small on purpose — over-stopping hurts recall.
_STOPWORDS = frozenset("""
a an the of to in on at by for with from into as is are was were be been being
do does did how what why when where which who whom whose that this these those
and or not no nor but if then than so such it its their they them you your we our
can could would should may might will shall about over under between work works
working explain explains tell me give mean means difference between using use used
""".split())


def _stem(tok):
    """Crude singularizer: drop a trailing plural/possessive 's' on longer tokens so
    'grovers'/'grover', 'qubits'/'qubit', 'ions'/'ion' collapse to one term. Applied to
    both documents and queries, so they always match in the same normalized space. We
    leave '...ss' words (e.g. 'process') alone."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _tokenize(text):
    """Lowercase, split on non-alphanumerics, drop stopwords and 1-char tokens, then
    singularize so plurals/possessives match their base form."""
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower())
            if len(t) > 1 and t not in _STOPWORDS]


def _doc_tokens(note):
    """A note's bag of words: title + body once, plus its keyword/alias phrases folded
    in `KEYWORD_DOC_WEIGHT` times so curated synonyms strengthen the note's vector."""
    toks = _tokenize(note.title) + _tokenize(note.text)
    for kw in note.keywords:
        toks += _tokenize(kw) * KEYWORD_DOC_WEIGHT
    return toks


def _build_index():
    """Build the TF-IDF matrix for the topic corpus once. Returns the note-id order,
    the vocabulary, the IDF vector, and the L2-normalized document matrix."""
    ids = list(TOPIC_NOTES)
    doc_tokens = {nid: _doc_tokens(TOPIC_NOTES[nid]) for nid in ids}
    vocab = {term: i for i, term in
             enumerate(sorted({t for toks in doc_tokens.values() for t in toks}))}
    n_docs, n_terms = len(ids), len(vocab)

    tf = np.zeros((n_docs, n_terms), dtype=float)
    for row, nid in enumerate(ids):
        for t in doc_tokens[nid]:
            tf[row, vocab[t]] += 1.0
    # Smoothed IDF: log((1+N)/(1+df)) + 1, the sklearn-style form (always positive).
    df = (tf > 0).sum(axis=0)
    idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0
    mat = tf * idf
    mat = _l2_normalize_rows(mat)
    return ids, vocab, idf, mat


def _l2_normalize_rows(mat):
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


_IDS, _VOCAB, _IDF, _DOC_MAT = _build_index()
# Doc-doc cosine similarity, precomputed for MMR diversity (rows are unit vectors).
_DOC_SIM = _DOC_MAT @ _DOC_MAT.T


@dataclass(frozen=True)
class Retrieval:
    """One scored hit: the note, its fused score, and a short human-readable reason
    (the overlapping query terms and any matched keyword phrase) for citations/debug."""
    note: TopicNote
    score: float
    why: str


def _query_vector(query):
    """TF-IDF vector for a free-text query, in the corpus vocabulary, L2-normalized.
    Unknown query terms (not in any note) are simply dropped."""
    vec = np.zeros(len(_VOCAB), dtype=float)
    for t in _tokenize(query):
        j = _VOCAB.get(t)
        if j is not None:
            vec[j] += 1.0
    vec *= _IDF
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _keyword_boosts(query):
    """Additive per-note bonus for an exact keyword/alias phrase appearing in the query
    (score fusion: lexical phrase match on top of the TF-IDF base). Padded query so a
    phrase only matches on whole-word boundaries."""
    q = f" {query.lower()} "
    boosts = np.zeros(len(_IDS), dtype=float)
    for row, nid in enumerate(_IDS):
        for kw in TOPIC_NOTES[nid].keywords:
            if f" {kw.lower()} " in q:
                boosts[row] += KEYWORD_BOOST
                break
    return boosts


def _why_matched(query, nid):
    """A short explanation: the query terms this note shares, plus any matched alias."""
    q_terms = set(_tokenize(query))
    shared = [t for t in dict.fromkeys(_tokenize(TOPIC_NOTES[nid].title)
                                       + _tokenize(TOPIC_NOTES[nid].text)) if t in q_terms]
    matched_kw = [kw for kw in TOPIC_NOTES[nid].keywords
                  if f" {kw.lower()} " in f" {query.lower()} "]
    bits = []
    if matched_kw:
        bits.append("matched: " + ", ".join(matched_kw))
    if shared:
        bits.append("terms: " + ", ".join(shared[:6]))
    return "; ".join(bits) or "weak lexical overlap"


def retrieve_topics(query, k=TOPIC_TOP_K, min_score=MIN_SCORE):
    """Rank the topic corpus against a free-text `query` and return up to `k`
    `Retrieval` hits, most relevant first. Abstains (returns []) when the best fused
    score is below `min_score`, so an off-topic or empty query pulls in nothing rather
    than a misleading note. Results are diversified with MMR so near-duplicate notes
    don't all appear together."""
    if not query or not query.strip():
        return []
    qvec = _query_vector(query)
    fused = (_DOC_MAT @ qvec) + _keyword_boosts(query)
    if fused.max(initial=0.0) < min_score:
        return []

    selected = _mmr_select(fused, k, min_score)
    return [Retrieval(TOPIC_NOTES[_IDS[i]], float(fused[i]), _why_matched(query, _IDS[i]))
            for i in selected]


def _mmr_select(fused, k, min_score):
    """Greedy Maximal Marginal Relevance over candidate rows whose fused score clears
    `min_score`: each pick maximizes MMR_LAMBDA*relevance - (1-lambda)*max similarity to
    already-picked notes, so we trade a little relevance for topical variety."""
    candidates = [i for i in range(len(_IDS)) if fused[i] >= min_score]
    candidates.sort(key=lambda i: fused[i], reverse=True)
    candidates = candidates[: max(k * 4, k)]  # only diversify among the plausible top

    selected = []
    while candidates and len(selected) < k:
        best_i, best_val = None, -np.inf
        for i in candidates:
            penalty = max((_DOC_SIM[i, j] for j in selected), default=0.0)
            val = MMR_LAMBDA * fused[i] - (1.0 - MMR_LAMBDA) * penalty
            if val > best_val:
                best_val, best_i = val, i
        selected.append(best_i)
        candidates.remove(best_i)
    return selected


def topic_reference_block(query, k=TOPIC_TOP_K):
    """Render the topic hits for a free-text question as a prompt reference block, or ""
    when the retriever abstains. Mirrors `reference_block` (the gate-note renderer) so
    the explainer sees one consistent 'Reference notes' format for both."""
    return _render_block([(h.note.title, h.note.text) for h in retrieve_topics(query, k=k)])


def combined_reference_block(spec, question=None, k=TOPIC_TOP_K):
    """The full reference block for the explainer, merging two corpora (multi-corpus
    retrieval): the gate/concept notes implied by the circuit, plus the topic notes
    retrieved for a free-text `question` when one is asked. Circuit notes come first
    (the canvas is the immediate context); topic notes follow, skipping any whose title
    a circuit note already covers. Returns "" if neither corpus yields anything."""
    chunks = list(retrieve(spec))
    seen = {title for title, _ in chunks}
    if question:
        for h in retrieve_topics(question, k=k):
            if h.note.title not in seen:
                chunks.append((h.note.title, h.note.text))
                seen.add(h.note.title)
    return _render_block(chunks)
