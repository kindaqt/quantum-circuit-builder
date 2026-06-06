"""Domain-logic tests for the Quantum Circuit Playground (`core`).

Covers the security boundary (gate whitelist + resource bounds in `validate`),
circuit correctness (including Qiskit's little-endian convention), the AI provider
registry, the persona registry, and the persona system-prompt assembly. These tests
call `core` directly and never go through the HTTP layer — the route tests live in
`test_api.py`.

No real network/LLM/quantum call is ever made: quantum-hardware behaviour is checked
by monkeypatching the module flag rather than dispatching to IBM, and the system
prompt is unit-tested directly (accurate vs. unreliable vs. Beaker's meeps-only
contract). Run with `pytest backend` (or `pytest` from inside `backend/`).
"""
import math

import pytest
from fastapi import HTTPException

import core
from core import CircuitSpec, Gate, validate


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def spec(num_qubits=2, gates=(), shots=1024, mode="sim"):
    return CircuitSpec(
        num_qubits=num_qubits,
        shots=shots,
        mode=mode,
        gates=[g if isinstance(g, Gate) else Gate(**g) for g in gates],
    )


def status_of(exc_info):
    return exc_info.value.status_code


# --------------------------------------------------------------------------- #
# validate(): the security boundary (whitelist + resource bounds)
# --------------------------------------------------------------------------- #
class TestValidate:
    def test_valid_circuit_passes(self):
        validate(spec(2, [{"name": "h", "qubits": [0]},
                          {"name": "cx", "qubits": [0, 1]}]))

    @pytest.mark.parametrize("n", [0, -1, core.MAX_QUBITS + 1])
    def test_qubit_count_out_of_range(self, n):
        with pytest.raises(HTTPException) as e:
            validate(spec(num_qubits=n))
        assert status_of(e) == 422

    @pytest.mark.parametrize("shots", [0, -5, core.MAX_SHOTS + 1])
    def test_shots_out_of_range(self, shots):
        with pytest.raises(HTTPException) as e:
            validate(spec(shots=shots))
        assert status_of(e) == 422

    def test_too_many_gates(self):
        gates = [{"name": "h", "qubits": [0]}] * (core.MAX_GATES + 1)
        with pytest.raises(HTTPException) as e:
            validate(spec(1, gates))
        assert status_of(e) == 422

    def test_unknown_mode_rejected(self):
        with pytest.raises(HTTPException) as e:
            validate(spec(mode="teleport"))
        assert status_of(e) == 422

    def test_unknown_gate_rejected(self):
        # The whole point of the whitelist: an un-listed QuantumCircuit method
        # (here a real one, `reset`) must never reach the getattr() dispatch.
        with pytest.raises(HTTPException) as e:
            validate(spec(1, [{"name": "reset", "qubits": [0]}]))
        assert status_of(e) == 422

    def test_dunder_gate_rejected(self):
        with pytest.raises(HTTPException) as e:
            validate(spec(1, [{"name": "__init__", "qubits": [0]}]))
        assert status_of(e) == 422

    @pytest.mark.parametrize("qubits", [[0], [0, 1, 2], [1, 1]])
    def test_wrong_arity_or_repeated_qubits(self, qubits):
        # cx needs exactly two *distinct* qubits.
        with pytest.raises(HTTPException) as e:
            validate(spec(3, [{"name": "cx", "qubits": qubits}]))
        assert status_of(e) == 422

    def test_out_of_range_qubit_index(self):
        with pytest.raises(HTTPException) as e:
            validate(spec(2, [{"name": "x", "qubits": [5]}]))
        assert status_of(e) == 422

    def test_param_gate_requires_finite_param(self):
        with pytest.raises(HTTPException) as e:
            validate(spec(1, [{"name": "rx", "qubits": [0], "param": None}]))
        assert status_of(e) == 422

    def test_param_gate_rejects_non_finite(self):
        with pytest.raises(HTTPException) as e:
            validate(spec(1, [{"name": "rz", "qubits": [0], "param": math.inf}]))
        assert status_of(e) == 422

    def test_param_gate_accepts_finite(self):
        validate(spec(1, [{"name": "ry", "qubits": [0], "param": math.pi / 3}]))

    def test_quantum_mode_blocked_when_hw_disabled(self, monkeypatch):
        monkeypatch.setattr(core, "ENABLE_QUANTUM_HW", False)
        with pytest.raises(HTTPException) as e:
            validate(spec(1, [{"name": "h", "qubits": [0]}], mode="quantum"))
        assert status_of(e) == 403


# --------------------------------------------------------------------------- #
# Circuit correctness — including Qiskit's little-endian convention
# --------------------------------------------------------------------------- #
class TestCircuitCorrectness:
    def _probs(self, sp):
        statevector, _bloch, _probs = core._simulated_state(sp)
        return {row["basis"]: row["prob"] for row in statevector}

    def test_hadamard_is_uniform(self):
        probs = self._probs(spec(1, [{"name": "h", "qubits": [0]}]))
        assert probs["0"] == pytest.approx(0.5, abs=1e-9)
        assert probs["1"] == pytest.approx(0.5, abs=1e-9)

    def test_x_on_qubit0_is_little_endian(self):
        # Qiskit is little-endian: qubit 0 is the *rightmost* bit. X on q0 of a
        # 2-qubit register flips the rightmost bit -> "01", not "10".
        probs = self._probs(spec(2, [{"name": "x", "qubits": [0]}]))
        assert probs.get("01", 0) == pytest.approx(1.0, abs=1e-9)
        assert "10" not in probs

    def test_x_on_qubit1_sets_left_bit(self):
        probs = self._probs(spec(2, [{"name": "x", "qubits": [1]}]))
        assert probs.get("10", 0) == pytest.approx(1.0, abs=1e-9)

    def test_bell_state_is_entangled(self):
        probs = self._probs(spec(2, [{"name": "h", "qubits": [0]},
                                      {"name": "cx", "qubits": [0, 1]}]))
        assert probs.get("00", 0) == pytest.approx(0.5, abs=1e-9)
        assert probs.get("11", 0) == pytest.approx(0.5, abs=1e-9)
        assert "01" not in probs and "10" not in probs

    def test_bloch_vector_of_plus_state(self):
        sp = spec(1, [{"name": "h", "qubits": [0]}])
        _sv, bloch, _ = core._simulated_state(sp)
        x, y, z = bloch[0]
        # |+> points along +X on the Bloch sphere.
        assert x == pytest.approx(1.0, abs=1e-9)
        assert y == pytest.approx(0.0, abs=1e-9)
        assert z == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# AI provider registry
# --------------------------------------------------------------------------- #
class TestProviderRegistry:
    def test_catalog_providers_have_handlers(self):
        for name in core._PROVIDER_CATALOG:
            assert name in core._AI_PROVIDERS

    def test_unknown_provider_not_ready(self):
        assert core.provider_ready("does-not-exist") is False

    def test_enabled_providers_are_ready(self):
        for name in core.enabled_providers():
            assert core.provider_ready(name) is True

    def test_default_provider_is_enabled_or_none(self):
        dp = core.default_provider()
        assert dp is None or dp in core.enabled_providers()

    def test_ai_enabled_matches_enabled_providers(self):
        assert core.ai_enabled() == bool(core.enabled_providers())

    def test_provider_not_ready_without_key(self, monkeypatch):
        # A key-needing provider that is toggled on but has no key is not usable.
        fake = dict(core.PROVIDERS)
        fake["anthropic"] = {**core.PROVIDERS["anthropic"],
                             "enabled_flag": True, "needs_key": True, "key": ""}
        monkeypatch.setattr(core, "PROVIDERS", fake)
        monkeypatch.setattr(core, "ENABLE_AI", True)
        assert core.provider_ready("anthropic") is False


# --------------------------------------------------------------------------- #
# Persona registry
# --------------------------------------------------------------------------- #
class TestPersonas:
    def test_default_persona_exists(self):
        assert core.DEFAULT_PERSONA in core.PERSONAS

    def test_every_persona_has_required_fields(self):
        for key, p in core.PERSONAS.items():
            assert p.get("name"), f"{key} missing name"
            assert p.get("voice"), f"{key} missing voice"

    def test_every_persona_has_a_blurb(self):
        # Blurbs power the hover tooltips; a missing one is a visible gap.
        missing = [k for k, p in core.PERSONAS.items() if not p.get("blurb")]
        assert missing == []

    def test_unreliable_personas_are_flagged(self):
        # Comedic personas opt out of the accurate teaching contract.
        for key in ("elon_musk", "jar_jar"):
            assert core.PERSONAS[key].get("accurate") is False


# --------------------------------------------------------------------------- #
# _system_prompt(): persona voice + the right behavioural contract
# --------------------------------------------------------------------------- #
class TestSystemPrompt:
    """The system prompt is a persona's voice followed by exactly one contract:
    accurate personas teach, comedic ones get the unreliable contract, and Beaker
    is special-cased to the meeps-only contract."""

    def test_accurate_persona_gets_teaching_contract(self):
        s = core._system_prompt("feynman")
        assert core._TEACHING_CONTRACT in s
        assert core._UNRELIABLE_CONTRACT not in s
        assert core._MEEP_CONTRACT not in s

    def test_unreliable_persona_gets_unreliable_contract(self):
        s = core._system_prompt("elon_musk")
        assert core._UNRELIABLE_CONTRACT in s
        assert core._TEACHING_CONTRACT not in s

    def test_beaker_gets_meep_contract_only(self):
        # Beaker can never explain anything — he only ever meeps.
        s = core._system_prompt("beaker")
        assert core._MEEP_CONTRACT in s
        assert core._TEACHING_CONTRACT not in s
        assert core._UNRELIABLE_CONTRACT not in s

    def test_unknown_persona_falls_back_to_professor(self):
        assert core._system_prompt("not-a-real-persona") == \
            core._system_prompt(core.DEFAULT_PERSONA)

    def test_none_persona_uses_default(self):
        assert core._system_prompt(None) == core._system_prompt(core.DEFAULT_PERSONA)
