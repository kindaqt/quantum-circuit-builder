"""HTTP-route tests for the Quantum Circuit Playground (`api`).

Drives the FastAPI app through a `TestClient` (the `client` fixture in
`conftest.py`) and covers the four endpoints: `/config`, `/simulate`, `/export`,
and `/explain`. The domain-logic tests (validation, circuits, providers, personas,
system prompt) live in `test_core.py`.

The tests never make a real network/LLM/quantum call: the `/explain` cases either
exercise validation paths that short-circuit *before* a provider handler runs, or
dispatch through a *fake* provider handler swapped in with monkeypatch (so the full
path is covered without touching a real LLM). Quantum-hardware behaviour is likewise
checked by monkeypatching the module flag rather than dispatching to IBM. Run with
`pytest backend` (or `pytest` from inside `backend/`).
"""
import pytest

import core


# --------------------------------------------------------------------------- #
# /config
# --------------------------------------------------------------------------- #
class TestConfigEndpoint:
    def test_config_ok_and_shape(self, client):
        r = client.get("/config")
        assert r.status_code == 200
        body = r.json()
        for field in ("quantum_enabled", "ai_enabled", "ai_providers",
                      "default_persona", "max_qubits"):
            assert field in body
        assert body["max_qubits"] == core.MAX_QUBITS
        assert body["default_persona"] == core.DEFAULT_PERSONA

    def test_config_never_leaks_api_keys(self, client):
        # No provider key should ever cross the wire. Plant a sentinel key and
        # assert it appears nowhere in the serialized /config payload.
        sentinel = "sk-SECRET-SENTINEL-DO-NOT-LEAK"
        fake = dict(core.PROVIDERS)
        for name, p in fake.items():
            fake[name] = {**p, "key": sentinel}
        original = core.PROVIDERS
        core.PROVIDERS = fake
        try:
            raw = client.get("/config").text
        finally:
            core.PROVIDERS = original
        assert sentinel not in raw

    def test_config_personas_present_when_ai_enabled(self, client):
        body = client.get("/config").json()
        if body["ai_enabled"]:
            keys = {p["key"] for p in body["personas"]}
            assert core.DEFAULT_PERSONA in keys
            # The /config persona objects expose only safe fields (never "voice").
            for p in body["personas"]:
                assert set(p) <= {"key", "name", "blurb"}


# --------------------------------------------------------------------------- #
# /simulate
# --------------------------------------------------------------------------- #
class TestSimulateEndpoint:
    def test_sim_mode_bell_counts_and_statevector(self, client):
        r = client.post("/simulate", json={
            "num_qubits": 2, "shots": 2048, "mode": "sim",
            "gates": [{"name": "h", "qubits": [0]},
                      {"name": "cx", "qubits": [0, 1]}],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "sim"
        assert body["extras_simulated"] is False
        # Bell state: counts only ever land on 00 / 11.
        assert set(body["counts"]).issubset({"00", "11"})
        assert sum(body["counts"].values()) == 2048
        bases = {row["basis"] for row in body["statevector"]}
        assert bases == {"00", "11"}

    def test_qsim_mode_measures_and_flags_simulated_extras(self, client):
        pytest.importorskip("qiskit_aer")
        r = client.post("/simulate", json={
            "num_qubits": 1, "shots": 512, "mode": "qsim",
            "gates": [{"name": "x", "qubits": [0]}],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "qsim"
        assert body["extras_simulated"] is True
        # Deterministic X: every shot measures "1".
        assert body["counts"] == {"1": 512}

    def test_invalid_gate_returns_422(self, client):
        r = client.post("/simulate", json={
            "num_qubits": 1, "mode": "sim",
            "gates": [{"name": "reset", "qubits": [0]}],
        })
        assert r.status_code == 422

    def test_quantum_mode_403_when_disabled(self, client, monkeypatch):
        monkeypatch.setattr(core, "ENABLE_QUANTUM_HW", False)
        r = client.post("/simulate", json={
            "num_qubits": 1, "mode": "quantum",
            "gates": [{"name": "h", "qubits": [0]}],
        })
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# /export
# --------------------------------------------------------------------------- #
class TestExportEndpoint:
    def test_export_returns_qiskit_and_qasm(self, client):
        r = client.post("/export", json={
            "num_qubits": 2, "mode": "sim",
            "gates": [{"name": "h", "qubits": [0]},
                      {"name": "cx", "qubits": [0, 1]}],
        })
        assert r.status_code == 200
        body = r.json()
        assert "QuantumCircuit" in body["qiskit"]
        assert "OPENQASM 3" in body["qasm"]
        # Both forms measure every qubit so they reproduce the histogram.
        assert "measure" in body["qiskit"].lower()
        assert "measure" in body["qasm"].lower()

    def test_export_validates_input(self, client):
        r = client.post("/export", json={
            "num_qubits": 1, "mode": "sim",
            "gates": [{"name": "definitely_not_a_gate", "qubits": [0]}],
        })
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# /explain — validation paths only (no real LLM call)
# --------------------------------------------------------------------------- #
class TestExplainValidation:
    BELL = [{"name": "h", "qubits": [0]}, {"name": "cx", "qubits": [0, 1]}]

    def _post(self, client, **extra):
        return client.post("/explain", json={
            "num_qubits": 2, "mode": "sim", "gates": self.BELL, **extra,
        })

    def test_disabled_returns_403(self, client, monkeypatch):
        monkeypatch.setattr(core, "enabled_providers", lambda: [])
        r = self._post(client)
        assert r.status_code == 403

    def test_unknown_persona_returns_422(self, client):
        if not core.ai_enabled():
            pytest.skip("AI explainer not configured in this environment")
        r = self._post(client, persona="not-a-real-persona")
        assert r.status_code == 422

    def test_unknown_provider_returns_422(self, client):
        if not core.ai_enabled():
            pytest.skip("AI explainer not configured in this environment")
        r = self._post(client, provider="not-a-real-provider")
        assert r.status_code == 422

    def test_unknown_model_returns_422(self, client):
        if not core.ai_enabled():
            pytest.skip("AI explainer not configured in this environment")
        provider = core.default_provider()
        r = self._post(client, provider=provider, model="not-a-real-model")
        assert r.status_code == 422

    def test_overlong_question_returns_422(self, client):
        if not core.ai_enabled():
            pytest.skip("AI explainer not configured in this environment")
        r = self._post(client, question="x" * (core.MAX_QUESTION_CHARS + 1))
        assert r.status_code == 422

    def test_overlong_history_returns_422(self, client):
        if not core.ai_enabled():
            pytest.skip("AI explainer not configured in this environment")
        history = [{"role": "user", "content": "hi"}
                   for _ in range(core.MAX_HISTORY_TURNS + 1)]
        r = self._post(client, history=history)
        assert r.status_code == 422

    def test_bad_history_role_returns_422(self, client):
        if not core.ai_enabled():
            pytest.skip("AI explainer not configured in this environment")
        r = self._post(client, history=[{"role": "system", "content": "x"}])
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# /explain — full dispatch with a fake provider (no real LLM call)
# --------------------------------------------------------------------------- #
class TestExplainDispatch:
    """Exercise the happy path end to end by swapping in a fake provider handler.
    This verifies the request reaches a handler with the correct system prompt and
    that circuits the UI now permits are accepted — including an initial-state-only
    circuit, which the client encodes as a single X gate and which the (now
    consistently enabled) 'Explain this circuit' button will send."""

    def _wire_fake_provider(self, monkeypatch):
        captured = {}

        def fake_handler(messages, system, model, key):
            captured["system"] = system
            captured["messages"] = messages
            captured["model"] = model
            return "FAKE EXPLANATION"

        monkeypatch.setattr(core, "_AI_PROVIDERS", {"anthropic": fake_handler})
        monkeypatch.setattr(core, "enabled_providers", lambda: ["anthropic"])
        monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
        monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
        return captured

    def test_happy_path_returns_explanation(self, client, monkeypatch):
        captured = self._wire_fake_provider(monkeypatch)
        r = client.post("/explain", json={
            "num_qubits": 2, "mode": "sim",
            "gates": [{"name": "h", "qubits": [0]}, {"name": "cx", "qubits": [0, 1]}],
            "persona": "feynman", "provider": "anthropic",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["explanation"] == "FAKE EXPLANATION"
        assert body["persona"] == "feynman"
        assert body["provider"] == "anthropic"
        # The handler received Feynman's voice + the accurate teaching contract.
        assert core._TEACHING_CONTRACT in captured["system"]

    def test_initial_state_only_circuit_is_accepted(self, client, monkeypatch):
        # A |1>-initialised qubit with no placed gates becomes a single X gate in
        # the payload. The Explain button now enables for this case, so /explain
        # must accept it rather than reject an otherwise-empty gate list.
        self._wire_fake_provider(monkeypatch)
        r = client.post("/explain", json={
            "num_qubits": 1, "mode": "sim",
            "gates": [{"name": "x", "qubits": [0]}],
            "persona": "professor", "provider": "anthropic",
        })
        assert r.status_code == 200
        assert r.json()["explanation"] == "FAKE EXPLANATION"

    def test_beaker_request_dispatches_meep_contract(self, client, monkeypatch):
        captured = self._wire_fake_provider(monkeypatch)
        r = client.post("/explain", json={
            "num_qubits": 1, "mode": "sim",
            "gates": [{"name": "h", "qubits": [0]}],
            "persona": "beaker", "provider": "anthropic",
        })
        assert r.status_code == 200
        # The provider was handed the meeps-only contract, never the teaching one.
        assert captured["system"] == core._system_prompt("beaker")
        assert core._MEEP_CONTRACT in captured["system"]
        assert core._TEACHING_CONTRACT not in captured["system"]
