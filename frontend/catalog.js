// ---- Gate catalog ----------------------------------------------------------
// The gate catalog and the predefined-algorithm presets — pure data, no DOM. The
// gate-name mapping (GATES[label].m) is a contract with the backend's ALLOWED
// whitelist; change both sides together.
//
// arity = number of qubits, param = takes a rotation/phase angle.
const GATES = {
  H:  { m: "h", arity: 1, desc: "Hadamard — creates superposition; maps |0⟩→(|0⟩+|1⟩)/√2." },
  X:  { m: "x", arity: 1, desc: "Pauli-X (NOT) — flips |0⟩↔|1⟩; π rotation about X." },
  Y:  { m: "y", arity: 1, desc: "Pauli-Y — bit+phase flip; π rotation about Y." },
  Z:  { m: "z", arity: 1, desc: "Pauli-Z — phase flip; sends |1⟩→−|1⟩." },
  S:  { m: "s", arity: 1, desc: "S (phase) — quarter turn; applies +i phase to |1⟩ (Z½)." },
  T:  { m: "t", arity: 1, desc: "T (π/8) — eighth turn; applies e^{iπ/4} phase to |1⟩ (Z¼)." },
  SDG: { m: "sdg", arity: 1, desc: "S† — inverse of S; applies −i phase to |1⟩." },
  TDG: { m: "tdg", arity: 1, desc: "T† — inverse of T; applies e^{−iπ/4} phase to |1⟩." },
  RX: { m: "rx", arity: 1, param: true, desc: "RX(θ) — rotation about the X axis by an adjustable angle." },
  RY: { m: "ry", arity: 1, param: true, desc: "RY(θ) — rotation about the Y axis by an adjustable angle." },
  RZ: { m: "rz", arity: 1, param: true, desc: "RZ(θ) — rotation about the Z axis by an adjustable angle." },
  CX: { m: "cx", arity: 2, desc: "CNOT — flips the target qubit when the control is |1⟩." },
  CZ: { m: "cz", arity: 2, desc: "Controlled-Z — applies a phase flip when both qubits are |1⟩." },
  SWAP: { m: "swap", arity: 2, desc: "SWAP — exchanges the states of two qubits." },
  CP: { m: "cp", arity: 2, param: true, desc: "Controlled-phase — adds an adjustable phase when both qubits are |1⟩." },
  CCX: { m: "ccx", arity: 3, desc: "Toffoli (CCX) — flips the target when both controls are |1⟩." },
  CSWAP: { m: "cswap", arity: 3, desc: "Fredkin (CSWAP) — swaps two qubits when the control is |1⟩." },
};

const P = Math.PI;
const a = (label, qubits, param = null) => ({ label, qubits, param });

// ---- Predefined algorithms (verified against Qiskit) ----------------------
// Each: required qubit count, gate sequence, and a one-line explanation.
const ALGOS = {
  "Bell": {
    n: 2,
    desc: "Maximally entangled pair: equal |00⟩ and |11⟩.",
    gates: [a("H", [0]), a("CX", [0, 1])],
  },
  "GHZ": {
    n: 3,
    desc: "3-qubit entanglement: equal |000⟩ and |111⟩.",
    gates: [a("H", [0]), a("CX", [0, 1]), a("CX", [1, 2])],
  },
  "Grover": {
    n: 2,
    desc: "2-qubit search marking |11⟩ — amplifies to 100%.",
    gates: [
      a("H", [0]), a("H", [1]), a("CZ", [0, 1]),
      a("H", [0]), a("H", [1]), a("X", [0]), a("X", [1]),
      a("CZ", [0, 1]), a("X", [0]), a("X", [1]), a("H", [0]), a("H", [1]),
    ],
  },
  "Deutsch–Jozsa": {
    n: 3,
    desc: "Balanced oracle: inputs q0,q1 measure |11⟩ (non-zero ⇒ balanced).",
    gates: [
      a("X", [2]), a("H", [0]), a("H", [1]), a("H", [2]),
      a("CX", [0, 2]), a("CX", [1, 2]),
      a("H", [0]), a("H", [1]),
    ],
  },
  "Bernstein–Vazirani": {
    n: 4,
    desc: "Recovers hidden string s=101 in one query (read q0,q1,q2).",
    gates: [
      a("X", [3]), a("H", [0]), a("H", [1]), a("H", [2]), a("H", [3]),
      a("CX", [0, 3]), a("CX", [2, 3]),
      a("H", [0]), a("H", [1]), a("H", [2]),
    ],
  },
  "QFT": {
    n: 3,
    desc: "3-qubit Quantum Fourier Transform.",
    gates: [
      a("H", [0]), a("CP", [1, 0], P / 2), a("CP", [2, 0], P / 4),
      a("H", [1]), a("CP", [2, 1], P / 2),
      a("H", [2]), a("SWAP", [0, 2]),
    ],
  },
  "Inverse QFT": {
    n: 3,
    desc: "Undoes the 3-qubit QFT.",
    gates: [
      a("SWAP", [0, 2]), a("H", [2]), a("CP", [2, 1], -P / 2),
      a("H", [1]), a("CP", [2, 0], -P / 4), a("CP", [1, 0], -P / 2),
      a("H", [0]),
    ],
  },
  "Phase estimation": {
    n: 4,
    desc: "Estimates phase 1/8 of a T gate — peak at |100⟩ (read q0q1q2 = 0.001).",
    gates: [
      a("X", [3]), a("H", [0]), a("H", [1]), a("H", [2]),
      a("CP", [0, 3], P / 4), a("CP", [1, 3], P / 2), a("CP", [2, 3], P),
      // inverse QFT on q0,q1,q2 (no final swaps -> clean single peak)
      a("H", [2]), a("CP", [2, 1], -P / 2),
      a("H", [1]), a("CP", [2, 0], -P / 4), a("CP", [1, 0], -P / 2),
      a("H", [0]),
    ],
  },
  "Shor (N=15, a=7)": {
    n: 8,
    desc: "Period-finding for 7 mod 15: 4 equal peaks ⇒ period r=4 ⇒ factors 3,5.",
    gates: shorGates(),
  },
  "Teleportation": {
    n: 3,
    desc: "Teleports the |1⟩ message from q0 onto q2 (deferred corrections; q2 always reads 1, q0/q1 random).",
    gates: [
      a("X", [0]),                       // message |1⟩ on q0
      a("H", [1]), a("CX", [1, 2]),      // entangled pair q1,q2
      a("CX", [0, 1]), a("H", [0]),      // Bell measurement basis on q0,q1
      a("CX", [1, 2]), a("CZ", [0, 2]),  // X/Z corrections (deferred measurement)
    ],
  },
  "Superdense coding": {
    n: 2,
    desc: "Sends 2 classical bits over 1 qubit using a shared pair — encodes 11, decodes to |11⟩.",
    gates: [
      a("H", [0]), a("CX", [0, 1]),      // shared Bell pair
      a("X", [0]), a("Z", [0]),          // Alice encodes bits 11 (X then Z)
      a("CX", [0, 1]), a("H", [0]),      // Bob decodes -> q1q0 = 11
    ],
  },
  "Simon (s=11)": {
    n: 4,
    desc: "Finds hidden period s=11: read inputs q0,q1 — outcomes split evenly over |00⟩ and |11⟩.",
    gates: [
      a("H", [0]), a("H", [1]),
      a("CX", [0, 2]), a("CX", [1, 3]),  // 2-to-1 oracle with period s=11
      a("CX", [0, 2]), a("CX", [0, 3]),
      a("H", [0]), a("H", [1]),
    ],
  },
  "Swap test": {
    n: 3,
    desc: "Measures state overlap: compares |+⟩ (q1) and |1⟩ (q2) — ancilla q0 reads 0 with prob 0.75.",
    gates: [
      a("H", [1]), a("X", [2]),          // prepare |+⟩ on q1, |1⟩ on q2
      a("H", [0]), a("CSWAP", [0, 1, 2]), a("H", [0]),
    ],
  },
};

function shorGates() {
  const g = [a("H", [0]), a("H", [1]), a("H", [2]), a("H", [3]), a("X", [4])];
  const cU = (c) => [
    a("CSWAP", [c, 4, 5]), a("CSWAP", [c, 5, 6]), a("CSWAP", [c, 6, 7]),
    a("CX", [c, 4]), a("CX", [c, 5]), a("CX", [c, 6]), a("CX", [c, 7]),
  ];
  g.push(...cU(0));                 // q0 controls U^1
  g.push(...cU(1), ...cU(1));       // q1 controls U^2  (q2,q3: U^4 = I, omitted)
  // inverse QFT on q0..q3 (no final swaps)
  g.push(
    a("H", [3]),
    a("CP", [3, 2], -P / 2), a("H", [2]),
    a("CP", [3, 1], -P / 4), a("CP", [2, 1], -P / 2), a("H", [1]),
    a("CP", [3, 0], -P / 8), a("CP", [2, 0], -P / 4), a("CP", [1, 0], -P / 2), a("H", [0]),
  );
  return g;
}
