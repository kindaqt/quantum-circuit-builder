"""Tests for Tier 3c: retention quiz generation + grading.

Three groups:

* Structured-output generation/grading (core.generate_quiz, core.grade_quiz,
  core.grade_mc_quiz) run everywhere — only need a stubbed AI handler, no database.
* Endpoint degradation tests confirm quiz routes return 503 when the optional
  database is absent, and 422/409 for contract violations.
* Live round-trip tests exercise the real DB and are skipped unless a migrated
  database is reachable (``make db-up && make migrate``).
"""
import json
import uuid

import pytest

import core
import db
import memory


# ---- Helpers ----------------------------------------------------------------
def _stub_quiz_provider(monkeypatch, reply: str):
    """Point the 'anthropic' provider at a fake handler and force it 'ready'."""
    monkeypatch.setattr(core, "_AI_PROVIDERS", {"anthropic": lambda *a, **k: reply})
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {**core.PROVIDERS, "anthropic": {
        **core.PROVIDERS.get("anthropic", {}),
        "models": ["stub-model"], "default_model": "stub-model", "key": "x",
    }})


# ---- generate_quiz (no database) --------------------------------------------
def test_generate_quiz_parses_clean_json(monkeypatch):
    _stub_quiz_provider(monkeypatch, json.dumps({
        "question": "What does a Hadamard gate do?",
        "topic": "Hadamard gate",
        "expected_answer": "It creates an equal superposition of |0⟩ and |1⟩.",
    }))
    result = core.generate_quiz("Recent context about Hadamard gates.", provider="anthropic")
    assert result["question"] == "What does a Hadamard gate do?"
    assert result["topic"] == "Hadamard gate"
    assert "superposition" in result["expected_answer"]


def test_generate_quiz_tolerates_fenced_json(monkeypatch):
    payload = json.dumps({
        "question": "What is entanglement?",
        "topic": "Entanglement",
        "expected_answer": "A correlation between qubits.",
    })
    _stub_quiz_provider(monkeypatch, f"Here is your quiz:\n```json\n{payload}\n```\n")
    result = core.generate_quiz("context", provider="anthropic")
    assert result["question"] == "What is entanglement?"


def test_generate_quiz_unparseable_yields_empty_strings(monkeypatch):
    _stub_quiz_provider(monkeypatch, "Sorry, I cannot generate a quiz right now.")
    result = core.generate_quiz("some context", provider="anthropic")
    assert result["question"] == ""
    assert result["topic"] == ""
    assert result["expected_answer"] == ""


def test_generate_quiz_all_keys_always_present(monkeypatch):
    _stub_quiz_provider(monkeypatch, json.dumps({"question": "What is a qubit?"}))
    result = core.generate_quiz("context", provider="anthropic")
    assert {"type", "question", "topic", "expected_answer", "options", "correct_option"} == set(result)
    assert result["topic"] == ""
    assert result["expected_answer"] == ""


# ---- generate_quiz MC variant -----------------------------------------------
def test_generate_quiz_parses_multiple_choice(monkeypatch):
    _stub_quiz_provider(monkeypatch, json.dumps({
        "type": "multiple_choice",
        "question": "Which gate creates superposition?",
        "topic": "Gates",
        "expected_answer": "The Hadamard gate.",
        "options": ["CNOT", "Hadamard", "Pauli-X", "T gate"],
        "correct_option": "B",
    }))
    result = core.generate_quiz("context", provider="anthropic")
    assert result["type"] == "multiple_choice"
    assert result["options"] == ["CNOT", "Hadamard", "Pauli-X", "T gate"]
    assert result["correct_option"] == "B"


def test_generate_quiz_mc_with_wrong_option_count_falls_back_to_text(monkeypatch):
    """MC with != 4 options must degrade to text."""
    _stub_quiz_provider(monkeypatch, json.dumps({
        "type": "multiple_choice",
        "question": "Q?",
        "topic": "T",
        "expected_answer": "A.",
        "options": ["only two", "options"],
        "correct_option": "A",
    }))
    result = core.generate_quiz("context", provider="anthropic")
    assert result["type"] == "text"
    assert result["options"] is None


def test_generate_quiz_mc_missing_correct_option_falls_back_to_text(monkeypatch):
    """MC without a valid correct_option letter must degrade to text."""
    _stub_quiz_provider(monkeypatch, json.dumps({
        "type": "multiple_choice",
        "question": "Q?",
        "topic": "T",
        "expected_answer": "A.",
        "options": ["a", "b", "c", "d"],
        "correct_option": "Z",   # invalid
    }))
    result = core.generate_quiz("context", provider="anthropic")
    assert result["type"] == "text"


# ---- grade_mc_quiz (no AI, no database) -------------------------------------
def test_grade_mc_correct():
    result = core.grade_mc_quiz("B", "B", ["CNOT", "Hadamard", "Pauli-X", "T gate"])
    assert result["grade"] == "correct"
    assert result["score"] == pytest.approx(1.0)


def test_grade_mc_incorrect_names_right_answer():
    opts = ["CNOT", "Hadamard", "Pauli-X", "T gate"]
    result = core.grade_mc_quiz("A", "B", opts)
    assert result["grade"] == "incorrect"
    assert result["score"] == pytest.approx(0.0)
    assert "Hadamard" in result["feedback"]   # names the correct option text


def test_grade_mc_case_insensitive():
    result = core.grade_mc_quiz("b", "B", None)
    assert result["grade"] == "correct"


def test_grade_mc_invalid_selection():
    result = core.grade_mc_quiz("Z", "A", None)
    assert result["grade"] == "incorrect"


# ---- grade_quiz (no database) -----------------------------------------------
def test_grade_quiz_correct(monkeypatch):
    _stub_quiz_provider(monkeypatch, json.dumps({
        "grade": "correct", "score": 1.0,
        "feedback": "Great — you nailed the core idea.",
    }))
    result = core.grade_quiz("Q", "A", "student's A", provider="anthropic")
    assert result["grade"] == "correct"
    assert result["score"] == 1.0
    assert result["feedback"]


def test_grade_quiz_partial(monkeypatch):
    _stub_quiz_provider(monkeypatch, json.dumps({
        "grade": "partial", "score": 0.5,
        "feedback": "You got the idea but missed the phase detail.",
    }))
    result = core.grade_quiz("Q", "A", "partial answer", provider="anthropic")
    assert result["grade"] == "partial"
    assert result["score"] == pytest.approx(0.5)


def test_grade_quiz_incorrect(monkeypatch):
    _stub_quiz_provider(monkeypatch, json.dumps({
        "grade": "incorrect", "score": 0.0,
        "feedback": "That describes a classical bit, not a qubit.",
    }))
    result = core.grade_quiz("Q", "A", "wrong answer", provider="anthropic")
    assert result["grade"] == "incorrect"
    assert result["score"] == pytest.approx(0.0)


def test_grade_quiz_invalid_grade_defaults_to_incorrect(monkeypatch):
    """A grade value that isn't correct/partial/incorrect must fall back to incorrect."""
    _stub_quiz_provider(monkeypatch, json.dumps({
        "grade": "maybe", "score": 0.7, "feedback": "Hmm.",
    }))
    result = core.grade_quiz("Q", "A", "ambiguous", provider="anthropic")
    assert result["grade"] == "incorrect"


def test_grade_quiz_score_clamped(monkeypatch):
    """Score is clamped to [0, 1] even if the model returns something out of range."""
    _stub_quiz_provider(monkeypatch, json.dumps({
        "grade": "correct", "score": 9.5, "feedback": "x",
    }))
    result = core.grade_quiz("Q", "A", "a", provider="anthropic")
    assert 0.0 <= result["score"] <= 1.0


def test_grade_quiz_unparseable_defaults_to_incorrect(monkeypatch):
    _stub_quiz_provider(monkeypatch, "I cannot grade this.")
    result = core.grade_quiz("Q", "A", "a", provider="anthropic")
    assert result["grade"] == "incorrect"
    assert result["score"] == pytest.approx(0.0)


# ---- Endpoint degradation: 503 when DB is absent ----------------------------
@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(db, "healthy", lambda: False)


def test_create_quiz_503_without_db(client, no_db):
    resp = client.post(f"/learner/{uuid.uuid4()}/quiz",
                       json={"context": "some context"})
    assert resp.status_code == 503


def test_answer_quiz_503_without_db(client, no_db):
    resp = client.post(f"/learner/{uuid.uuid4()}/quiz/1/answer",
                       json={"learner_answer": "yes"})
    assert resp.status_code == 503


def test_list_quizzes_503_without_db(client, no_db):
    assert client.get(f"/learner/{uuid.uuid4()}/quizzes").status_code == 503


def test_create_quiz_unknown_learner_returns_404(client, monkeypatch):
    """An unknown learner id must 404, not 500."""
    monkeypatch.setattr(db, "healthy", lambda: True)
    monkeypatch.setattr(memory, "get_learner", lambda _: None)
    resp = client.post(f"/learner/{uuid.uuid4()}/quiz",
                       json={"context": "some context"})
    assert resp.status_code == 404


def test_create_quiz_empty_context_triggers_general_quiz(client, monkeypatch):
    """Empty/absent context is no longer an error — it triggers a general quantum quiz."""
    import uuid as _uuid
    monkeypatch.setattr(db, "healthy", lambda: True)
    learner_id = _uuid.uuid4()
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": str(learner_id)})
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "provider_ready", lambda n: True)
    monkeypatch.setattr(core, "PROVIDERS", {
        "anthropic": {"key": "k", "models": ["m"], "default_model": "m", "label": "A"}
    })
    monkeypatch.setattr(core, "generate_quiz", lambda ctx, prov, mdl, general=False: {
        "type": "text", "question": "What is superposition?",
        "topic": "Superposition", "expected_answer": "A qubit can be in both states.",
        "options": None, "correct_option": None,
    })
    monkeypatch.setattr(memory, "create_quiz", lambda *a, **k: {
        "id": 1, "type": "text", "question": "What is superposition?",
        "topic": "Superposition", "expected_answer": "A qubit can be in both states.",
        "options": None, "correct_option": None,
    })
    resp = client.post(f"/learner/{learner_id}/quiz", json={})
    assert resp.status_code == 200


def test_create_quiz_general_flag_set_when_no_context(client, monkeypatch):
    """Verify the `general=True` flag is forwarded to generate_quiz when context is absent."""
    import uuid as _uuid
    monkeypatch.setattr(db, "healthy", lambda: True)
    learner_id = _uuid.uuid4()
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": str(learner_id)})
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "provider_ready", lambda n: True)
    monkeypatch.setattr(core, "PROVIDERS", {
        "anthropic": {"key": "k", "models": ["m"], "default_model": "m", "label": "A"}
    })
    received = {}
    def _fake_generate(ctx, prov, mdl, general=False):
        received["general"] = general
        return {
            "type": "text", "question": "q", "topic": "t",
            "expected_answer": "a", "options": None, "correct_option": None,
        }
    monkeypatch.setattr(core, "generate_quiz", _fake_generate)
    monkeypatch.setattr(memory, "create_quiz", lambda *a, **k: {
        "id": 1, "type": "text", "question": "q", "topic": "t",
        "expected_answer": "a", "options": None, "correct_option": None,
    })
    client.post(f"/learner/{learner_id}/quiz", json={})
    assert received.get("general") is True


def test_create_quiz_general_false_when_context_present(client, monkeypatch):
    """When context is provided, general=False is forwarded to generate_quiz."""
    import uuid as _uuid
    monkeypatch.setattr(db, "healthy", lambda: True)
    learner_id = _uuid.uuid4()
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": str(learner_id)})
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "provider_ready", lambda n: True)
    monkeypatch.setattr(core, "PROVIDERS", {
        "anthropic": {"key": "k", "models": ["m"], "default_model": "m", "label": "A"}
    })
    received = {}
    def _fake_generate(ctx, prov, mdl, general=False):
        received["general"] = general
        return {
            "type": "text", "question": "q", "topic": "t",
            "expected_answer": "a", "options": None, "correct_option": None,
        }
    monkeypatch.setattr(core, "generate_quiz", _fake_generate)
    monkeypatch.setattr(memory, "create_quiz", lambda *a, **k: {
        "id": 1, "type": "text", "question": "q", "topic": "t",
        "expected_answer": "a", "options": None, "correct_option": None,
    })
    client.post(f"/learner/{learner_id}/quiz", json={"context": "We just covered the H gate."})
    assert received.get("general") is False


def test_general_quiz_skips_embedding_call(client, monkeypatch):
    """When context is absent (general quiz), the embedding function must not be called.

    A general quiz has no anchor text for semantic search, so calling embed() on an
    empty string would be wasteful and wrong.  The endpoint short-circuits the
    semantic-augmentation block entirely when general=True.
    """
    import uuid as _uuid
    monkeypatch.setattr(db, "healthy", lambda: True)
    learner_id = _uuid.uuid4()
    monkeypatch.setattr(memory, "get_learner", lambda _: {"id": str(learner_id)})
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "provider_ready", lambda n: True)
    monkeypatch.setattr(core, "PROVIDERS", {
        "anthropic": {"key": "k", "models": ["m"], "default_model": "m", "label": "A"}
    })
    monkeypatch.setattr(core, "generate_quiz", lambda ctx, prov, mdl, general=False: {
        "type": "text", "question": "q", "topic": "t",
        "expected_answer": "a", "options": None, "correct_option": None,
    })
    monkeypatch.setattr(memory, "create_quiz", lambda *a, **k: {
        "id": 1, "type": "text", "question": "q", "topic": "t",
        "expected_answer": "a", "options": None, "correct_option": None,
    })

    import api as _api_module
    embed_calls = []
    import embeddings as _emb
    monkeypatch.setattr(_emb, "available", lambda: True)
    monkeypatch.setattr(_emb, "embed", lambda text: embed_calls.append(text) or [0.0] * 384)

    client.post(f"/learner/{learner_id}/quiz", json={})  # no context → general quiz
    assert embed_calls == [], (
        "embed() must not be called for a general quiz (no context to embed)"
    )


def test_answer_quiz_already_answered_is_409(client, monkeypatch):
    """Submitting a second answer to an already-graded quiz must be 409."""
    import datetime
    quiz_id = 42
    learner_id = uuid.uuid4()
    monkeypatch.setattr(db, "healthy", lambda: True)
    monkeypatch.setattr(memory, "get_quiz", lambda qid: {
        "id": quiz_id,
        "learner_id": str(learner_id),
        "question": "Q", "expected_answer": "A",
        "answered_at": datetime.datetime.utcnow(),
    })
    resp = client.post(f"/learner/{learner_id}/quiz/{quiz_id}/answer",
                       json={"learner_answer": "re-answer"})
    assert resp.status_code == 409


def test_answer_quiz_empty_answer_is_422(client, monkeypatch):
    learner_id = uuid.uuid4()
    monkeypatch.setattr(db, "healthy", lambda: True)
    monkeypatch.setattr(memory, "get_quiz", lambda qid: {
        "id": 1,
        "learner_id": str(learner_id),
        "question": "Q", "expected_answer": "A",
        "answered_at": None,
    })
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    resp = client.post(f"/learner/{learner_id}/quiz/1/answer",
                       json={"learner_answer": "   "})
    assert resp.status_code == 422


# ---- Live database (skipped unless a migrated DB is reachable) --------------
live_db = pytest.mark.skipif(
    not db.healthy(),
    reason="no reachable migrated database (set QCB_DATABASE_URL, run `make db-up && make migrate`)",
)


@live_db
def test_quiz_round_trip(client, monkeypatch):
    """Create a learner → generate a quiz → submit an answer → check the row."""
    monkeypatch.setattr(core, "ai_enabled", lambda: True)
    monkeypatch.setattr(core, "provider_ready", lambda name: name == "anthropic")
    monkeypatch.setattr(core, "default_provider", lambda: "anthropic")
    monkeypatch.setattr(core, "PROVIDERS", {**core.PROVIDERS, "anthropic": {
        **core.PROVIDERS.get("anthropic", {}),
        "models": ["stub-model"], "default_model": "stub-model", "key": "x",
    }})
    monkeypatch.setattr(core, "_AI_PROVIDERS", {
        "anthropic": lambda *a, **k: json.dumps({
            "question": "What is a qubit?",
            "topic": "Qubits",
            "expected_answer": "A two-level quantum system.",
        }),
    })

    learner_id = client.post("/learner").json()["id"]
    quiz_resp = client.post(f"/learner/{learner_id}/quiz",
                            json={"context": "We talked about qubits."})
    assert quiz_resp.status_code == 200
    qdata = quiz_resp.json()
    assert qdata["question"] == "What is a qubit?"
    quiz_id = qdata["quiz_id"]

    # expected_answer must NOT be in the generate response (don't reveal it yet).
    assert "expected_answer" not in qdata

    # Grade the answer.
    monkeypatch.setattr(core, "_AI_PROVIDERS", {
        "anthropic": lambda *a, **k: json.dumps({
            "grade": "correct", "score": 1.0,
            "feedback": "Spot on.",
        }),
    })
    answer_resp = client.post(f"/learner/{learner_id}/quiz/{quiz_id}/answer",
                              json={"learner_answer": "A two-state quantum system."})
    assert answer_resp.status_code == 200
    adata = answer_resp.json()
    assert adata["grade"] == "correct"
    assert adata["score"] == pytest.approx(1.0)
    assert adata["expected_answer"]  # revealed after answering

    # Submitting again must 409.
    dup = client.post(f"/learner/{learner_id}/quiz/{quiz_id}/answer",
                      json={"learner_answer": "again"})
    assert dup.status_code == 409

    # List quizzes.
    quizzes = client.get(f"/learner/{learner_id}/quizzes").json()
    assert any(q["id"] == quiz_id for q in quizzes)

    with db.connection() as conn:
        conn.execute("DELETE FROM learners WHERE id = %s", (learner_id,))
