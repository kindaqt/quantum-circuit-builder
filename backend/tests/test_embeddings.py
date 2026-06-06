"""Tests for Tier 3d: local embeddings + semantic recall.

Three groups:

* Embedding module unit tests — monkeypatch the model singleton; no
  network, no DB, no real sentence-transformers inference.
* Memory recall unit tests — monkeypatch the repository functions;
  verify merge/deduplication/sort logic without a live database.
* API integration tests — confirm that /explain stores interactions and
  /quiz augments context, and that both degrade gracefully on failure.
* Live embedding test — skipped unless sentence-transformers is installed
  and the model is cached locally.
"""
import datetime
import uuid

import pytest

import core
import db
import embeddings
import memory


# ---- Helpers -----------------------------------------------------------------

class _FakeModel:
    """Minimal SentenceTransformer stand-in that returns deterministic vectors."""

    def encode(self, texts, *, normalize_embeddings=False):
        import numpy as np
        if isinstance(texts, str):
            return np.zeros(embeddings.EMBED_DIMS, dtype=np.float32)
        return np.zeros((len(texts), embeddings.EMBED_DIMS), dtype=np.float32)


def _install_fake_model(monkeypatch):
    """Force embeddings.available() to return True with a zero-vector model."""
    monkeypatch.setattr(embeddings, "_model", _FakeModel())
    monkeypatch.setattr(embeddings, "_model_error", None)


def _disable_embeddings(monkeypatch):
    """Force embeddings.available() to return False."""
    monkeypatch.setattr(embeddings, "_model", None)
    monkeypatch.setattr(embeddings, "_model_error", Exception("not installed"))


# ---- Embedding module unit tests ---------------------------------------------

def test_available_true_when_model_loaded(monkeypatch):
    _install_fake_model(monkeypatch)
    assert embeddings.available() is True


def test_available_false_when_load_failed(monkeypatch):
    _disable_embeddings(monkeypatch)
    assert embeddings.available() is False


def test_embed_returns_list_of_floats(monkeypatch):
    _install_fake_model(monkeypatch)
    result = embeddings.embed("What is a qubit?")
    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result)


def test_embed_returns_correct_dims(monkeypatch):
    _install_fake_model(monkeypatch)
    result = embeddings.embed("superposition")
    assert len(result) == embeddings.EMBED_DIMS


def test_embed_returns_none_when_unavailable(monkeypatch):
    _disable_embeddings(monkeypatch)
    assert embeddings.embed("anything") is None


def test_embed_batch_returns_list_of_lists(monkeypatch):
    _install_fake_model(monkeypatch)
    result = embeddings.embed_batch(["qubit", "entanglement", "Hadamard"])
    assert isinstance(result, list)
    assert len(result) == 3
    assert len(result[0]) == embeddings.EMBED_DIMS


def test_embed_batch_returns_none_when_unavailable(monkeypatch):
    _disable_embeddings(monkeypatch)
    assert embeddings.embed_batch(["a", "b"]) is None


def test_embed_batch_single_consistent_with_embed(monkeypatch):
    """A one-element batch should produce the same vector as a direct embed call."""
    _install_fake_model(monkeypatch)
    single = embeddings.embed("Hadamard gate")
    batch = embeddings.embed_batch(["Hadamard gate"])
    assert single == batch[0]


# ---- Memory recall unit tests ------------------------------------------------

def _make_row(id_, role, content, minutes_ago):
    return {
        "id": id_,
        "learner_id": str(uuid.uuid4()),
        "session_id": None,
        "role": role,
        "content": content,
        "persona": None,
        "created_at": (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)
        ),
    }


def test_recall_context_turns_recent_only_when_no_embedding(monkeypatch):
    """When query_embedding is None, only recency is used (no semantic call)."""
    recent = [_make_row(1, "assistant", "Hadamard creates superposition.", 5)]
    monkeypatch.setattr(memory, "get_recent_interactions", lambda lid, limit: recent)

    called = []
    monkeypatch.setattr(memory, "search_interactions",
                        lambda *a, **k: called.append(1) or [])

    result = memory.recall_context_turns("lid", None)
    assert result == recent
    assert not called  # search_interactions must NOT be called


def test_recall_context_turns_merges_recent_and_semantic(monkeypatch):
    """Recent + semantic rows should all appear in the merged result."""
    r1 = _make_row(1, "user",      "What is a qubit?",   10)
    r2 = _make_row(2, "assistant", "A two-level system.", 9)
    r3 = _make_row(3, "assistant", "Entanglement intro.", 2)

    monkeypatch.setattr(memory, "get_recent_interactions", lambda lid, limit: [r1, r2])
    monkeypatch.setattr(memory, "search_interactions",     lambda *a, **k: [r3])

    result = memory.recall_context_turns("lid", [0.0] * embeddings.EMBED_DIMS)
    ids = [r["id"] for r in result]
    assert 1 in ids and 2 in ids and 3 in ids


def test_recall_context_turns_deduplicates(monkeypatch):
    """A row that appears in both recent and semantic must only appear once."""
    r1 = _make_row(1, "user",      "What is entanglement?", 8)
    r2 = _make_row(2, "assistant", "Correlation of qubits.", 7)

    # r2 appears in both lists
    monkeypatch.setattr(memory, "get_recent_interactions", lambda lid, limit: [r1, r2])
    monkeypatch.setattr(memory, "search_interactions",     lambda *a, **k: [r2])

    result = memory.recall_context_turns("lid", [0.0] * embeddings.EMBED_DIMS)
    assert len(result) == 2
    assert [r["id"] for r in result].count(2) == 1


def test_recall_context_turns_sorted_chronologically(monkeypatch):
    """Merged results must come out oldest-first regardless of source order."""
    old  = _make_row(10, "assistant", "Old explanation.", 60)
    mid  = _make_row(11, "user",      "Follow-up question.", 30)
    new  = _make_row(12, "assistant", "Recent explanation.", 5)

    # recent returns new+mid, semantic returns old (in a weird order)
    monkeypatch.setattr(memory, "get_recent_interactions", lambda lid, limit: [new, mid])
    monkeypatch.setattr(memory, "search_interactions",     lambda *a, **k: [old])

    result = memory.recall_context_turns("lid", [0.0] * embeddings.EMBED_DIMS)
    assert [r["id"] for r in result] == [10, 11, 12]


def test_recall_context_turns_empty_when_no_interactions(monkeypatch):
    monkeypatch.setattr(memory, "get_recent_interactions", lambda lid, limit: [])
    monkeypatch.setattr(memory, "search_interactions",     lambda *a, **k: [])
    result = memory.recall_context_turns("lid", [0.0] * embeddings.EMBED_DIMS)
    assert result == []


# ---- API integration tests ---------------------------------------------------

def _stub_explain(monkeypatch, explanation="test explanation"):
    """Set up the minimum stubs needed for a successful POST /explain."""
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {**core.PROVIDERS, "anthropic": {
        **core.PROVIDERS.get("anthropic", {}),
        "models": ["stub-model"], "default_model": "stub-model", "key": "x",
    }})
    monkeypatch.setattr(core, "explain_circuit", lambda *a, **k: explanation)


def test_explain_stores_both_turns_when_learner_id_set(client, monkeypatch):
    """When a learner_id is provided and the DB is up, both turns are stored."""
    _stub_explain(monkeypatch)
    _install_fake_model(monkeypatch)
    monkeypatch.setattr(db, "available", lambda: True)

    learner_id = str(uuid.uuid4())
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": learner_id})

    saved = []
    monkeypatch.setattr(memory, "save_interaction",
                        lambda *a, **k: saved.append({"role": a[1]}) or {})

    resp = client.post("/explain", json={
        "num_qubits": 1,
        "gates": [{"name": "h", "qubits": [0]}],
        "learner_id": learner_id,
        "question": "What does the H gate do?",
    })
    assert resp.status_code == 200
    roles = [s["role"] for s in saved]
    assert "user" in roles
    assert "assistant" in roles


def test_explain_stores_only_assistant_turn_when_no_question(client, monkeypatch):
    """Without a question the user turn is skipped; the explanation still stored."""
    _stub_explain(monkeypatch)
    _install_fake_model(monkeypatch)
    monkeypatch.setattr(db, "available", lambda: True)

    learner_id = str(uuid.uuid4())
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": learner_id})

    saved = []
    monkeypatch.setattr(memory, "save_interaction",
                        lambda *a, **k: saved.append({"role": a[1]}) or {})

    resp = client.post("/explain", json={
        "num_qubits": 1,
        "gates": [{"name": "h", "qubits": [0]}],
        "learner_id": learner_id,
        # no "question" key
    })
    assert resp.status_code == 200
    assert saved  # at least the assistant turn
    assert all(s["role"] == "assistant" for s in saved)


def test_explain_does_not_store_when_no_learner_id(client, monkeypatch):
    """No learner_id → no interaction storage attempt."""
    _stub_explain(monkeypatch)
    monkeypatch.setattr(db, "available", lambda: True)

    called = []
    monkeypatch.setattr(memory, "save_interaction", lambda *a, **k: called.append(1))

    resp = client.post("/explain", json={
        "num_qubits": 1,
        "gates": [{"name": "h", "qubits": [0]}],
    })
    assert resp.status_code == 200
    assert not called


def test_explain_does_not_fail_when_save_interaction_raises(client, monkeypatch):
    """A crash in save_interaction must not break the /explain response."""
    _stub_explain(monkeypatch)
    _install_fake_model(monkeypatch)
    monkeypatch.setattr(db, "available", lambda: True)

    learner_id = str(uuid.uuid4())
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": learner_id})
    monkeypatch.setattr(memory, "save_interaction",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    resp = client.post("/explain", json={
        "num_qubits": 1,
        "gates": [{"name": "h", "qubits": [0]}],
        "learner_id": learner_id,
        "question": "What is superposition?",
    })
    assert resp.status_code == 200


def test_explain_skips_store_when_db_unavailable(client, monkeypatch):
    """When db.available() is False the store path is not entered at all."""
    _stub_explain(monkeypatch)
    monkeypatch.setattr(db, "available", lambda: False)

    called = []
    monkeypatch.setattr(memory, "save_interaction", lambda *a, **k: called.append(1))
    monkeypatch.setattr(memory, "get_learner", lambda _: None)  # should not be reached

    learner_id = str(uuid.uuid4())
    resp = client.post("/explain", json={
        "num_qubits": 1,
        "gates": [{"name": "h", "qubits": [0]}],
        "learner_id": learner_id,
        "question": "What is superposition?",
    })
    assert resp.status_code == 200
    assert not called


def test_quiz_augments_context_with_past_turns(client, monkeypatch):
    """When past interactions exist, the quiz prompt includes [Past conversation]."""
    monkeypatch.setattr(db, "healthy", lambda: True)
    monkeypatch.setattr(db, "available", lambda: True)

    learner_id = str(uuid.uuid4())
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": str(learner_id)})

    past_turn = _make_row(99, "assistant", "A qubit is a two-level system.", 60)
    monkeypatch.setattr(memory, "recall_context_turns",
                        lambda *a, **k: [past_turn])

    _install_fake_model(monkeypatch)

    captured_contexts = []

    def fake_generate_quiz(context, provider, model, general=False):
        captured_contexts.append(context)
        return {
            "type": "text", "question": "Q?", "topic": "T",
            "expected_answer": "A.", "options": None, "correct_option": None,
        }

    monkeypatch.setattr(core, "generate_quiz", fake_generate_quiz)
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {**core.PROVIDERS, "anthropic": {
        **core.PROVIDERS.get("anthropic", {}),
        "models": ["stub-model"], "default_model": "stub-model", "key": "x",
    }})
    monkeypatch.setattr(memory, "create_quiz",
                        lambda *a, **k: {
                            "id": 1, "type": "text", "question": "Q?", "topic": "T",
                            "expected_answer": "A.", "options": None, "correct_option": None,
                        })

    resp = client.post(f"/learner/{learner_id}/quiz",
                       json={"context": "We talked about qubits."})
    assert resp.status_code == 200
    assert captured_contexts
    assert "[Past conversation]" in captured_contexts[0]


def test_quiz_context_augmentation_fails_gracefully(client, monkeypatch):
    """If recall_context_turns raises, the quiz is generated from client context alone."""
    monkeypatch.setattr(db, "healthy", lambda: True)
    monkeypatch.setattr(db, "available", lambda: True)

    learner_id = str(uuid.uuid4())
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": str(learner_id)})
    monkeypatch.setattr(memory, "recall_context_turns",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db error")))
    _install_fake_model(monkeypatch)

    captured_contexts = []

    def fake_generate_quiz(context, provider, model, general=False):
        captured_contexts.append(context)
        return {
            "type": "text", "question": "Q?", "topic": "T",
            "expected_answer": "A.", "options": None, "correct_option": None,
        }

    monkeypatch.setattr(core, "generate_quiz", fake_generate_quiz)
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {**core.PROVIDERS, "anthropic": {
        **core.PROVIDERS.get("anthropic", {}),
        "models": ["stub-model"], "default_model": "stub-model", "key": "x",
    }})
    monkeypatch.setattr(memory, "create_quiz",
                        lambda *a, **k: {
                            "id": 1, "type": "text", "question": "Q?", "topic": "T",
                            "expected_answer": "A.", "options": None, "correct_option": None,
                        })

    resp = client.post(f"/learner/{learner_id}/quiz",
                       json={"context": "We talked about qubits."})
    assert resp.status_code == 200
    assert captured_contexts
    # Context fell back to the client-supplied string without [Past conversation]
    assert "[Past conversation]" not in captured_contexts[0]


# ---- Live embedding (skipped unless model is locally cached) -----------------

live_embeddings = pytest.mark.skipif(
    not embeddings.available(),
    reason="sentence-transformers not installed or model not yet cached",
)


@live_embeddings
def test_live_embed_correct_dims():
    vec = embeddings.embed("What is a quantum gate?")
    assert vec is not None
    assert len(vec) == embeddings.EMBED_DIMS


@live_embeddings
def test_live_embed_normalised():
    """Normalised embeddings have unit L2 norm (within float tolerance)."""
    import math
    vec = embeddings.embed("Hadamard gate creates superposition")
    assert vec is not None
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-5


@live_embeddings
def test_live_semantic_similarity():
    """Semantically similar sentences should be closer than unrelated ones."""
    v_qubit  = embeddings.embed("A qubit is a two-level quantum system")
    v_qubit2 = embeddings.embed("Qubits can exist in superposition of 0 and 1")
    v_food   = embeddings.embed("I enjoy eating pasta for dinner")
    assert v_qubit and v_qubit2 and v_food

    def cosine(a, b):
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na  = math.sqrt(sum(x * x for x in a))
        nb  = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb)

    assert cosine(v_qubit, v_qubit2) > cosine(v_qubit, v_food)
