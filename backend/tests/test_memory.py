"""Tests for the learner-profile layer (Tier 3b).

Three groups:

* Structured-output extraction (`core.extract_profile`) and prompt assembly
  (`_profile_block`, the onboarding prompt) run everywhere — they only need a
  stubbed AI handler, no database.
* Endpoint degradation tests pin the contract that the memory routes return a
  clean 503 (not a 500) when the optional database is absent.
* Live round-trip tests exercise the real repository + endpoints and are skipped
  unless a migrated database is reachable (`make db-up && make migrate`).
"""
import json
import uuid

import pytest

import core
import db
import memory


# ---- Structured-output extraction (no database needed) ---------------------
def _stub_provider(monkeypatch, reply: str):
    """Point the 'anthropic' provider at a fake handler that returns `reply`, and
    force it 'ready' so extract_profile/onboarding will dispatch to it."""
    captured = {}

    def handler(messages, system, model, key):
        captured["messages"] = messages
        captured["system"] = system
        return reply

    monkeypatch.setattr(core, "_AI_PROVIDERS", {"anthropic": handler})
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    return captured


def test_extract_profile_parses_clean_json(monkeypatch):
    _stub_provider(monkeypatch, json.dumps({
        "level": "complete beginner",
        "background": "web developer, rusty on linear algebra",
        "interests": "entanglement",
        "goals": "build intuition",
    }))
    profile = core.extract_profile("I'm a web dev, totally new to quantum.", provider="anthropic")
    assert profile == {
        "level": "complete beginner",
        "background": "web developer, rusty on linear algebra",
        "interests": "entanglement",
        "goals": "build intuition",
    }


def test_extract_profile_tolerates_prose_and_fences(monkeypatch):
    _stub_provider(monkeypatch, (
        "Sure! Here's the profile:\n```json\n"
        '{"level": "knows the basic gates", "background": "", '
        '"interests": "Shor\'s algorithm", "goals": ""}\n```\nHope that helps!'
    ))
    profile = core.extract_profile("I know H, X, CNOT and want to get to Shor.", provider="anthropic")
    assert profile["level"] == "knows the basic gates"
    assert profile["interests"] == "Shor's algorithm"
    assert profile["background"] == ""
    assert profile["goals"] == ""


def test_extract_profile_unparseable_yields_empty_fields(monkeypatch):
    _stub_provider(monkeypatch, "I couldn't figure that out, sorry.")
    profile = core.extract_profile("...", provider="anthropic")
    assert profile == {"level": "", "background": "", "interests": "", "goals": ""}


def test_extract_profile_all_keys_present_even_when_partial(monkeypatch):
    _stub_provider(monkeypatch, json.dumps({"level": "beginner"}))
    profile = core.extract_profile("just starting", provider="anthropic")
    assert set(profile) == {"level", "background", "interests", "goals"}
    assert profile["level"] == "beginner"
    assert profile["background"] == ""


# ---- Prompt assembly (no database, no AI) ----------------------------------
def test_profile_block_renders_only_nonempty_fields():
    block = core._profile_block({
        "display_name": "Ada",
        "level": "beginner",
        "background": "",
        "interests": "entanglement",
        "goals": "",
    })
    assert "Ada" in block
    assert "beginner" in block
    assert "entanglement" in block
    # Empty fields must not leave dangling labels.
    assert "Background" not in block
    assert "Goals" not in block


def test_profile_block_empty_when_no_fields():
    assert core._profile_block(None) == ""
    assert core._profile_block({}) == ""
    assert core._profile_block({"level": "", "background": ""}) == ""


def test_onboarding_prompt_ignores_circuit_and_question():
    spec = core.CircuitSpec(num_qubits=2, gates=[{"name": "h", "qubits": [0]}])
    onboarding = core._build_prompt(spec, "what is this circuit?", ground=True, onboarding=True)
    assert onboarding == core._ONBOARDING_INSTRUCTION
    # A normal prompt does reference the circuit, so the two differ.
    normal = core._build_prompt(spec, "what is this circuit?", ground=True, onboarding=False)
    assert onboarding != normal


# ---- Endpoint degradation: clean 503 when the DB is absent -----------------
@pytest.fixture
def no_db(monkeypatch):
    """Force the memory layer to look unreachable, regardless of local .env."""
    monkeypatch.setattr(db, "healthy", lambda: False)


def test_create_learner_503_without_db(client, no_db):
    assert client.post("/learner").status_code == 503


def test_get_learner_503_without_db(client, no_db):
    assert client.get(f"/learner/{uuid.uuid4()}").status_code == 503


def test_onboarding_503_without_db(client, no_db):
    resp = client.post(f"/learner/{uuid.uuid4()}/onboarding", json={"answer": "hi"})
    assert resp.status_code == 503


def test_update_profile_503_without_db(client, no_db):
    resp = client.put(f"/learner/{uuid.uuid4()}/profile", json={"level": "beginner"})
    assert resp.status_code == 503


def test_malformed_learner_id_is_422_not_500(client):
    """A non-UUID path id is rejected at the schema (422) before any DB call, so it
    can never reach the UUID column and surface as a 500."""
    assert client.get("/learner/not-a-uuid").status_code == 422
    assert client.put("/learner/not-a-uuid/profile", json={"level": "x"}).status_code == 422
    assert client.post("/learner/not-a-uuid/onboarding", json={"answer": "hi"}).status_code == 422


def test_explain_still_works_when_db_down(client, monkeypatch):
    """A learner_id on /explain must not break the explainer when the DB is down:
    the profile lookup is best-effort and explain proceeds without it."""
    monkeypatch.setattr(db, "available", lambda: False)
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "explain_circuit", lambda *a, **k: "explained")

    def boom(_):  # if this is called, the best-effort guard failed
        raise AssertionError("get_learner must not be called when db.available() is False")

    monkeypatch.setattr(memory, "get_learner", boom)
    resp = client.post("/explain", json={
        "num_qubits": 1,
        "gates": [{"name": "h", "qubits": [0]}],
        "learner_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 200
    assert resp.json()["explanation"] == "explained"


# ---- Live database (skipped unless a migrated DB is reachable) -------------
live_db = pytest.mark.skipif(
    not db.healthy(),
    reason="no reachable migrated database (set QCB_DATABASE_URL, run `make db-up && make migrate`)",
)


@live_db
def test_create_get_and_update_round_trip(client):
    created = client.post("/learner").json()
    learner_id = created["id"]
    assert created["onboarded_at"] is None

    # Direct profile edit (no onboarding stamp).
    updated = client.put(f"/learner/{learner_id}/profile", json={
        "display_name": "Ada", "level": "beginner",
    }).json()
    assert updated["display_name"] == "Ada"
    assert updated["level"] == "beginner"
    assert updated["onboarded_at"] is None

    fetched = client.get(f"/learner/{learner_id}").json()
    assert fetched["display_name"] == "Ada"

    # Cleanup.
    with db.connection() as conn:
        conn.execute("DELETE FROM learners WHERE id = %s", (learner_id,))


@live_db
def test_get_unknown_learner_404(client):
    assert client.get(f"/learner/{uuid.uuid4()}").status_code == 404


@live_db
def test_onboarding_extracts_and_stamps(client, monkeypatch):
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {**core.PROVIDERS, "anthropic": {
        **core.PROVIDERS.get("anthropic", {}),
        "models": ["stub-model"], "default_model": "stub-model", "key": "x",
    }})
    monkeypatch.setattr(core, "_AI_PROVIDERS", {"anthropic": lambda *a, **k: json.dumps({
        "level": "beginner", "background": "physics", "interests": "", "goals": "",
    })})

    learner_id = client.post("/learner").json()["id"]
    resp = client.post(f"/learner/{learner_id}/onboarding", json={"answer": "I'm a physics student, new to QC."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["level"] == "beginner"
    assert body["background"] == "physics"
    assert body["onboarded_at"] is not None

    with db.connection() as conn:
        conn.execute("DELETE FROM learners WHERE id = %s", (learner_id,))
