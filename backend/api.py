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
import json as _json
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import the domain module in a way that works both as a package (uvicorn loads
# this as ``backend.api``) and as a top-level module (pytest puts ``backend/`` on
# sys.path and imports ``core`` directly).
try:
    from . import core, db, embeddings, memory
except ImportError:  # pragma: no cover - exercised by the top-level import path
    import core
    import db
    import embeddings
    import memory


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
    # Optional tutor-memory hooks. learner_id pulls a stored profile into the
    # system prompt (best-effort — explain still works if the DB is down).
    # onboarding flips the reply to a session-start intake question instead of a
    # circuit explanation.
    learner_id: str | None = None
    onboarding: bool = False


class OnboardingAnswer(BaseModel):
    """The student's free-text intake answer, plus the provider/model to extract
    with (so the structured-output call matches whatever the UI is using)."""
    answer: str
    provider: str | None = None
    model: str | None = None


class ProfileUpdate(BaseModel):
    """A direct edit of the stored profile fields (the UI's profile form). Every
    field is optional; only the ones present are written."""
    display_name: str | None = None
    level: str | None = None
    background: str | None = None
    interests: str | None = None
    goals: str | None = None


class QuizRequest(BaseModel):
    """Ask the tutor to generate a retention quiz question from recent context.
    `context` is a plain-text excerpt of recent conversation turns — the model
    uses it to pick a relevant topic. `topic` is an optional override hint."""
    context: str | None = None
    topic: str | None = None
    provider: str | None = None
    model: str | None = None


class QuizAnswerRequest(BaseModel):
    """Submit a student's free-text answer to an open quiz question."""
    learner_answer: str
    provider: str | None = None
    model: str | None = None


class HandoffRequest(BaseModel):
    """Request a persona-switch handoff: a farewell from the outgoing persona and
    a greeting from the incoming one, both generated in-character by the LLM.
    Passing the same key for both is a no-op (returns empty strings).
    ``history`` is the recent conversation (newest last); when present both
    messages reference what was actually discussed."""
    from_persona: str
    to_persona: str
    provider: str | None = None
    model: str | None = None
    history: list[ChatTurn] | None = None


def _store_interaction(
    learner_id: str, question: str, answer: str, persona: str
) -> None:
    """Best-effort persist a Q&A turn pair for long-term recall.

    Embeds both turns when sentence-transformers is available; stores with
    NULL embeddings otherwise (rows still appear in recency-based recall).
    Never raises — a failure here must never break the explainer response.
    """
    try:
        if question:
            q_emb = embeddings.embed(question) if embeddings.available() else None
            memory.save_interaction(
                learner_id, "user", question, persona=persona, embedding=q_emb
            )
        a_emb = embeddings.embed(answer) if embeddings.available() else None
        memory.save_interaction(
            learner_id, "assistant", answer, persona=persona, embedding=a_emb
        )
    except Exception:  # pragma: no cover - best-effort, DB hiccup or embed error
        pass


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
    # Best-effort profile lookup: a known learner_id pulls their stored profile into
    # the system prompt, but a down/absent DB must never break the explainer — so we
    # only try when the layer is wired and reachable, and ignore a missing learner.
    profile = None
    if spec.learner_id and db.available():
        try:
            profile = memory.get_learner(spec.learner_id)
        except Exception:  # pragma: no cover - DB hiccup mid-request; degrade silently
            profile = None
    explanation = core.explain_circuit(
        spec, question or None, history, persona, provider, model,
        profile=profile, onboarding=spec.onboarding,
    )
    # Persist this turn for long-term recall. Best-effort: a DB/embed failure
    # must never break the explainer response; only store when the learner is
    # known and the database layer is wired up.
    if spec.learner_id and db.available():
        _store_interaction(spec.learner_id, question, explanation, persona)
    # Never return the API key — only the explanation and which model/persona produced it.
    return {"explanation": explanation, "provider": provider, "model": model, "persona": persona}


@app.post("/explain/stream")
def explain_stream(spec: ExplainSpec):
    """Streaming variant of /explain — returns an SSE stream of text chunks.

    Events (each newline-separated, each preceded by ``data: ``):
      ``{"delta": "..."}``  — one or more text chunks as they arrive from the model
      ``{"done": true, "provider": "...", "model": "...", "persona": "..."}``  — final event
    On error inside the generator:
      ``{"error": "..."}``  — describes the problem; the stream then ends

    The validation and auth logic is identical to ``/explain``; the response
    format is ``text/event-stream`` so the browser can read it incrementally.
    """
    if not core.ai_enabled():
        raise HTTPException(
            403, "AI explainer is disabled (set QCB_ENABLE_AI=true and configure a provider)"
        )
    core.validate(spec)
    question = (spec.question or "").strip()
    if len(question) > core.MAX_QUESTION_CHARS:
        raise HTTPException(422, f"question too long (max {core.MAX_QUESTION_CHARS} characters)")
    persona = spec.persona or core.DEFAULT_PERSONA
    if persona not in core.PERSONAS:
        raise HTTPException(422, f"unknown persona '{persona}'")
    provider = spec.provider or core.default_provider()
    if not provider or not core.provider_ready(provider):
        raise HTTPException(422, f"unknown or disabled provider '{spec.provider}'")
    model = spec.model or core.PROVIDERS[provider]["default_model"]
    if model not in core.PROVIDERS[provider]["models"]:
        raise HTTPException(422, f"unknown model '{model}' for provider '{provider}'")
    history = _clean_history(spec.history)
    profile = None
    if spec.learner_id and db.available():
        try:
            profile = memory.get_learner(spec.learner_id)
        except Exception:  # pragma: no cover - DB hiccup mid-request; degrade silently
            profile = None

    def _generate():
        accum: list[str] = []
        try:
            for chunk in core.explain_circuit_stream(
                spec, question or None, history, persona, provider, model,
                profile=profile, onboarding=spec.onboarding,
            ):
                accum.append(chunk)
                yield f"data: {_json.dumps({'delta': chunk})}\n\n"
        except Exception as exc:  # pragma: no cover - surface provider errors cleanly
            yield f"data: {_json.dumps({'error': str(exc)})}\n\n"
            return

        full_text = "".join(accum).strip()
        # Persist best-effort: never break the stream if the DB/embed step fails.
        if spec.learner_id and db.available():
            _store_interaction(spec.learner_id, question, full_text, persona)

        yield (
            f"data: {_json.dumps({'done': True, 'provider': provider, 'model': model, 'persona': persona})}\n\n"
        )

    return StreamingResponse(_generate(), media_type="text/event-stream")


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
        # Whether the tutor-memory features (learner profiles, quizzes) are wired
        # up. True when the psycopg driver is installed and QCB_DATABASE_URL is
        # set; the DB doesn't have to be reachable yet (the routes 503 if it is
        # down at request time, which is a better UX than hiding the buttons).
        "memory_enabled": db.available(),
        # Whether local sentence embeddings are loaded (semantic recall active).
        "embeddings_enabled": embeddings.available(),
        # How many professor turns between automatic quiz triggers (env QCB_QUIZ_INTERVAL).
        "quiz_interval": core.QUIZ_INTERVAL,
    }


@app.get("/health")
def health():
    """Liveness + optional-database status.

    The app is always 'ok' (the core playground needs no database); the `db`
    block reports whether the optional memory layer is wired up and reachable,
    which is what `make db-up` / `make migrate` and manual testing check.
    """
    return {"status": "ok", "db": db.status()}


@app.post("/persona/handoff")
def handoff(body: HandoffRequest):
    """Return a farewell from the outgoing persona and a greeting from the incoming one.

    Both are short, in-character LLM messages (under ~400 characters each).
    Returns ``{"farewell": str, "greeting": str}``; both empty when from == to.
    """
    if not core.ai_enabled():
        raise HTTPException(
            403,
            "AI explainer is disabled (set QCB_ENABLE_AI=true and configure a provider)",
        )
    if body.from_persona not in core.PERSONAS:
        raise HTTPException(422, f"unknown persona '{body.from_persona}'")
    if body.to_persona not in core.PERSONAS:
        raise HTTPException(422, f"unknown persona '{body.to_persona}'")
    provider = body.provider or core.default_provider()
    if not provider or not core.provider_ready(provider):
        raise HTTPException(422, f"unknown or disabled provider '{body.provider}'")
    model = body.model or core.PROVIDERS[provider]["default_model"]
    if model not in core.PROVIDERS[provider]["models"]:
        raise HTTPException(422, f"unknown model '{model}' for provider '{provider}'")
    history = _clean_history(body.history) if body.history else None
    return core.persona_handoff(body.from_persona, body.to_persona, provider, model, history=history)


def _require_db():
    """Gate the memory endpoints on a reachable database. The core playground works
    without one, but profiles need to persist — so these routes 503 (not 500) when
    the optional layer isn't wired up or the server is unreachable."""
    if not db.healthy():
        raise HTTPException(503, "Tutor memory is unavailable (no database configured or reachable)")


@app.post("/learner")
def create_learner():
    """Mint a fresh, empty learner. The client stores the returned id (localStorage)
    and sends it back on /explain and the onboarding/profile routes."""
    _require_db()
    return memory.create_learner()


@app.get("/learner/{learner_id}")
def get_learner(learner_id: UUID):
    """Return a stored learner profile, or 404 if the id is unknown."""
    _require_db()
    learner = memory.get_learner(learner_id)
    if learner is None:
        raise HTTPException(404, "no such learner")
    return learner


@app.post("/learner/{learner_id}/onboarding")
def onboard_learner(learner_id: UUID, body: OnboardingAnswer):
    """Turn a student's free-text intake answer into a structured profile and store
    it (stamping onboarded_at). Uses the same provider/model as the explainer, so the
    extraction matches the UI's selection."""
    _require_db()
    if not core.ai_enabled():
        raise HTTPException(403, "AI is disabled (set QCB_ENABLE_AI=true and configure a provider)")
    if memory.get_learner(learner_id) is None:
        raise HTTPException(404, "no such learner")
    answer = (body.answer or "").strip()
    if not answer:
        raise HTTPException(422, "answer is empty")
    if len(answer) > core.MAX_QUESTION_CHARS:
        raise HTTPException(422, f"answer too long (max {core.MAX_QUESTION_CHARS} characters)")
    provider = (body.provider or core.default_provider())
    if not provider or not core.provider_ready(provider):
        raise HTTPException(422, f"unknown or disabled provider '{body.provider}'")
    model = (body.model or core.PROVIDERS[provider]["default_model"])
    if model not in core.PROVIDERS[provider]["models"]:
        raise HTTPException(422, f"unknown model '{model}' for provider '{provider}'")
    fields = core.extract_profile(answer, provider, model)
    return memory.save_profile(learner_id, fields, mark_onboarded=True)


@app.put("/learner/{learner_id}/profile")
def update_profile(learner_id: UUID, body: ProfileUpdate):
    """Directly edit stored profile fields (the UI's profile form). Only fields the
    client sends are written; this does not re-stamp onboarded_at."""
    _require_db()
    if memory.get_learner(learner_id) is None:
        raise HTTPException(404, "no such learner")
    fields = body.model_dump(exclude_none=True)
    return memory.save_profile(learner_id, fields, mark_onboarded=False)


@app.post("/learner/{learner_id}/quiz")
def create_quiz(learner_id: UUID, body: QuizRequest):
    """Generate a retention quiz question from recent tutoring context and store
    it. The client passes the last few assistant turns as `context`; the model
    picks a topic and produces the question + a reference answer."""
    _require_db()
    if not core.ai_enabled():
        raise HTTPException(403, "AI is disabled (set QCB_ENABLE_AI=true and configure a provider)")
    if memory.get_learner(str(learner_id)) is None:
        raise HTTPException(404, "no such learner")
    context = (body.context or "").strip()
    # Empty context is not an error: it triggers a general quantum-computing quiz
    # rather than a session-specific retention check.
    general = not context
    if not general:
        if len(context) > core._MAX_QUIZ_CONTEXT_CHARS * 2:
            context = context[:core._MAX_QUIZ_CONTEXT_CHARS * 2]
        # Augment the client-supplied context with semantically relevant turns
        # from past sessions.  A context-free general quiz has no anchor for
        # semantic search, so we only do this when context is present.
        # Best-effort: a failure here falls back to the client context alone.
        try:
            query_emb = embeddings.embed(context) if embeddings.available() else None
            past_turns = memory.recall_context_turns(str(learner_id), query_emb)
            if past_turns:
                past_text = "\n".join(
                    ("Student" if t["role"] == "user" else "Professor")
                    + ": " + t["content"]
                    for t in past_turns
                )
                context = f"[Past conversation]\n{past_text}\n\n[Recent session]\n{context}"
                context = context[:core._MAX_QUIZ_CONTEXT_CHARS * 2]
        except Exception:
            pass  # fall back to client context

    provider = (body.provider or core.default_provider())
    if not provider or not core.provider_ready(provider):
        raise HTTPException(422, f"unknown or disabled provider '{body.provider}'")
    model = (body.model or core.PROVIDERS[provider]["default_model"])
    if model not in core.PROVIDERS[provider]["models"]:
        raise HTTPException(422, f"unknown model '{model}' for provider '{provider}'")
    result = core.generate_quiz(context, provider, model, general=general)
    question = result.get("question", "").strip()
    if not question:
        raise HTTPException(502, "The AI could not generate a quiz question from this context.")
    quiz = memory.create_quiz(
        str(learner_id),
        question=question,
        topic=result.get("topic", ""),
        expected_answer=result.get("expected_answer", ""),
        quiz_type=result.get("type", "text"),
        options=result.get("options"),
        correct_option=result.get("correct_option"),
    )
    # Return what the UI needs. For MC, include options (so the frontend can render
    # radio buttons) but never expose correct_option or expected_answer before grading.
    resp = {
        "quiz_id": quiz["id"],
        "type": quiz.get("type", "text"),
        "question": quiz["question"],
        "topic": quiz["topic"],
    }
    if quiz.get("type") == "multiple_choice" and quiz.get("options"):
        resp["options"] = quiz["options"]
    return resp


@app.post("/learner/{learner_id}/quiz/{quiz_id}/answer")
def answer_quiz(learner_id: UUID, quiz_id: int, body: QuizAnswerRequest):
    """Grade the student's answer, store the result, and return the grading.

    Multiple-choice questions are graded by direct comparison (no LLM call —
    always available even when the AI feature is toggled off). Open-ended text
    questions are LLM-graded and require the AI feature to be enabled."""
    _require_db()
    quiz = memory.get_quiz(quiz_id)
    if quiz is None or str(quiz["learner_id"]) != str(learner_id):
        raise HTTPException(404, "no such quiz for this learner")
    if quiz.get("answered_at") is not None:
        raise HTTPException(409, "this quiz has already been answered")
    learner_answer = (body.learner_answer or "").strip()
    if not learner_answer:
        raise HTTPException(422, "learner_answer is required")
    if len(learner_answer) > memory.MAX_QUIZ_ANSWER_CHARS:
        raise HTTPException(422, f"answer too long (max {memory.MAX_QUIZ_ANSWER_CHARS} chars)")

    quiz_type = quiz.get("type") or "text"

    if quiz_type == "multiple_choice":
        # No LLM needed — deterministic comparison against the stored correct letter.
        correct = quiz.get("correct_option") or ""
        options = quiz.get("options")  # list or None (psycopg returns JSONB as Python obj)
        result = core.grade_mc_quiz(learner_answer, correct, options)
    else:
        # Open-ended: LLM grades the free-text answer.
        if not core.ai_enabled():
            raise HTTPException(403, "AI is disabled (set QCB_ENABLE_AI=true and configure a provider)")
        provider = (body.provider or core.default_provider())
        if not provider or not core.provider_ready(provider):
            raise HTTPException(422, f"unknown or disabled provider '{body.provider}'")
        model = (body.model or core.PROVIDERS[provider]["default_model"])
        if model not in core.PROVIDERS[provider]["models"]:
            raise HTTPException(422, f"unknown model '{model}' for provider '{provider}'")
        result = core.grade_quiz(
            question=quiz["question"],
            expected_answer=quiz.get("expected_answer") or "",
            learner_answer=learner_answer,
            provider=provider,
            model=model,
        )

    updated = memory.answer_quiz(
        quiz_id,
        learner_answer=learner_answer,
        grade=result["grade"],
        score=result["score"],
        feedback=result["feedback"],
    )
    return {
        "quiz_id": updated["id"],
        "type": quiz_type,
        "grade": updated["grade"],
        "score": updated["score"],
        "feedback": updated["feedback"],
        "expected_answer": updated["expected_answer"],
    }


@app.get("/learner/{learner_id}/quizzes")
def list_quizzes(learner_id: UUID, limit: int = 20):
    """Return a learner's recent quiz history, newest first. Caps at 50."""
    _require_db()
    if memory.get_learner(str(learner_id)) is None:
        raise HTTPException(404, "no such learner")
    limit = min(max(1, limit), 50)
    rows = memory.get_learner_quizzes(str(learner_id), limit=limit)
    # Redact expected_answer for unanswered quizzes so students can't peek.
    for row in rows:
        if row.get("answered_at") is None:
            row["expected_answer"] = None
    return rows


@app.get("/learner/{learner_id}/interactions")
def get_interactions(
    learner_id: UUID,
    limit: int = 20,
    before_id: int | None = None,
):
    """Return a page of stored conversation turns for a learner, oldest-first.

    Use ``before_id`` for cursor-based pagination: pass the ``id`` of the oldest
    interaction already displayed to load the next (older) page. Capped at 50
    per request. Returns ``[]`` when the learner has no stored interactions.
    """
    _require_db()
    if memory.get_learner(str(learner_id)) is None:
        raise HTTPException(404, "no such learner")
    limit = min(max(1, limit), 50)
    return memory.get_interactions_page(str(learner_id), limit=limit, before_id=before_id)


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
