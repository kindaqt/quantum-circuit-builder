"""Data access for the tutor's memory features.

A thin repository over the Postgres tables defined in backend/migrations/. It
owns the SQL; backend/db.py owns the connection. Callers should confirm the
database is usable (``db.available()`` / ``db.healthy()``) before calling in —
these functions assume a working connection and let psycopg errors propagate, so
the HTTP layer can turn an outage into a clean 503.

Step (b) covers the learner profile; step (c) adds the quiz repository.
Sessions and embeddings are added by later steps.
"""
from typing import Any

try:
    from . import db
except ImportError:  # pragma: no cover - top-level import path (pytest)
    import db

# Dict rows make the call sites read like the JSON we return. Imported lazily-ish
# (guarded) so importing this module never hard-fails when psycopg is absent; the
# functions below only run once the database is confirmed available anyway.
try:
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only without the driver
    dict_row: Any = None

# The editable profile fields, in the order we present them. Kept in one place so
# the repository, the prompt-injection block, and the onboarding extractor agree.
PROFILE_FIELDS = ("display_name", "level", "background", "interests", "goals")

# Per-field length cap — generous for free text, but bounded so a profile can't
# bloat the prompt or the row. Applied on write.
MAX_FIELD_CHARS = 2000


def _clip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:MAX_FIELD_CHARS]


def create_learner() -> dict:
    """Insert a fresh, empty learner and return the row (id + timestamps)."""
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO learners DEFAULT VALUES "
                "RETURNING id, created_at, onboarded_at, "
                "display_name, level, background, interests, goals"
            )
            return cur.fetchone()


def get_learner(learner_id: str) -> dict | None:
    """Return the learner row as a dict, or None if no such id."""
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, created_at, onboarded_at, "
                "display_name, level, background, interests, goals "
                "FROM learners WHERE id = %s",
                (learner_id,),
            )
            return cur.fetchone()


def save_profile(learner_id: str, fields: dict, *, mark_onboarded: bool = True) -> dict | None:
    """Update the supplied profile fields for a learner and return the new row.

    Only keys in PROFILE_FIELDS are honored (others are ignored), and each value
    is trimmed/clipped. Passing mark_onboarded stamps onboarded_at on first
    completion (kept stable on re-runs via COALESCE). Returns None if the id
    doesn't exist.
    """
    updates = {k: _clip(fields.get(k)) for k in PROFILE_FIELDS if k in fields}
    set_clauses = [f"{col} = %s" for col in updates]
    params: list[Any] = list(updates.values())
    if mark_onboarded:
        # Keep the original onboarding timestamp if it's already set.
        set_clauses.append("onboarded_at = COALESCE(onboarded_at, now())")
    if not set_clauses:
        return get_learner(learner_id)
    params.append(learner_id)
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"UPDATE learners SET {', '.join(set_clauses)} WHERE id = %s "
                "RETURNING id, created_at, onboarded_at, "
                "display_name, level, background, interests, goals",
                params,
            )
            return cur.fetchone()


# ---- Quiz repository (Tier 3c) -----------------------------------------------
# Quizzes are generated from recent tutoring context, stored while pending, then
# graded by the LLM and updated with the result. The quiz id is a BIGSERIAL so it
# can be used as a simple integer path parameter without leaking UUIDs.

_QUIZ_COLS = (
    "id", "learner_id", "type", "topic", "question", "expected_answer",
    "options", "correct_option",
    "learner_answer", "grade", "correct", "score", "feedback",
    "asked_at", "answered_at",
)
_QUIZ_SELECT = ", ".join(_QUIZ_COLS)

# Maximum characters for learner_answer, feedback — stored verbatim but bounded.
MAX_QUIZ_ANSWER_CHARS = 2000
MAX_QUIZ_FEEDBACK_CHARS = 500


def create_quiz(learner_id: str, question: str, topic: str,
                expected_answer: str, quiz_type: str = "text",
                options: list | None = None,
                correct_option: str | None = None) -> dict:
    """Insert a new unanswered quiz question and return the row.

    For multiple-choice questions pass ``quiz_type='multiple_choice'``,
    ``options`` (list of 4 strings), and ``correct_option`` ('A'–'D').
    """
    import json as _json
    opts_json = _json.dumps(options) if options else None
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO quizzes "
                "  (learner_id, type, question, topic, expected_answer, options, correct_option) "
                f"VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING {_QUIZ_SELECT}",
                (
                    learner_id,
                    quiz_type,
                    question[:2000],
                    topic[:200] if topic else None,
                    expected_answer[:4000] if expected_answer else None,
                    opts_json,
                    correct_option,
                ),
            )
            return cur.fetchone()


def get_quiz(quiz_id: int) -> dict | None:
    """Return a quiz row by id, or None if not found."""
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_QUIZ_SELECT} FROM quizzes WHERE id = %s",
                (quiz_id,),
            )
            return cur.fetchone()


def answer_quiz(quiz_id: int, learner_answer: str, grade: str,
                score: float, feedback: str) -> dict | None:
    """Store the student's answer + grading result. Returns updated row or None."""
    correct = grade == "correct"  # True / False; partial counts as False for the boolean
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"UPDATE quizzes SET "
                "    learner_answer = %s, "
                "    grade = %s, "
                "    correct = %s, "
                "    score = %s, "
                "    feedback = %s, "
                "    answered_at = now() "
                f"WHERE id = %s RETURNING {_QUIZ_SELECT}",
                (
                    learner_answer[:MAX_QUIZ_ANSWER_CHARS],
                    grade,
                    correct,
                    max(0.0, min(1.0, float(score))),
                    feedback[:MAX_QUIZ_FEEDBACK_CHARS] if feedback else None,
                    quiz_id,
                ),
            )
            return cur.fetchone()


def get_learner_quizzes(learner_id: str, limit: int = 20) -> list[dict]:
    """Return the most recent quizzes for a learner, newest first."""
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_QUIZ_SELECT} FROM quizzes "
                "WHERE learner_id = %s ORDER BY asked_at DESC LIMIT %s",
                (learner_id, limit),
            )
            return cur.fetchall()


# ---- Interaction repository (Tier 3d) ----------------------------------------
# Stores individual Q&A turns from tutoring sessions so they can be recalled
# across sessions. The embedding column (added in migration 0004) enables
# pgvector cosine-similarity search; rows with NULL embeddings are visible to
# recency search but not to semantic search.

_INTERACTION_COLS = (
    "id", "learner_id", "session_id", "role", "content", "persona", "created_at",
)
_INTERACTION_SELECT = ", ".join(_INTERACTION_COLS)

# Cap stored content so embeddings are focused and the prompt doesn't balloon.
MAX_INTERACTION_CHARS = 4000


def save_interaction(
    learner_id: str,
    role: str,
    content: str,
    *,
    persona: str | None = None,
    session_id: str | None = None,
    embedding: list[float] | None = None,
) -> dict:
    """Persist a single conversation turn, optionally with its dense embedding.

    ``role`` must be ``'user'`` or ``'assistant'`` (enforced by the DB check
    constraint). ``embedding`` should be a normalised ``EMBED_DIMS``-dim float
    list produced by ``embeddings.embed()``; pass ``None`` to store the row
    without a vector (it will still appear in recency-based recall).
    """
    emb_placeholder = "%s::vector" if embedding is not None else "%s"
    emb_str: str | None = (
        "[" + ",".join(str(f) for f in embedding) + "]"
        if embedding is not None else None
    )
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO interactions "
                "  (learner_id, session_id, role, content, persona, embedding) "
                f"VALUES (%s, %s, %s, %s, %s, {emb_placeholder}) "
                f"RETURNING {_INTERACTION_SELECT}",
                (
                    learner_id,
                    session_id,
                    role,
                    content[:MAX_INTERACTION_CHARS],
                    persona,
                    emb_str,
                ),
            )
            return cur.fetchone()


def get_recent_interactions(learner_id: str, *, limit: int = 6) -> list[dict]:
    """Return the ``limit`` most recent interactions, oldest-first.

    Reversed so callers can feed them directly into a context window as a
    chronological transcript.
    """
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_INTERACTION_SELECT} FROM interactions "
                "WHERE learner_id = %s ORDER BY created_at DESC LIMIT %s",
                (learner_id, limit),
            )
            rows = cur.fetchall()
    return list(reversed(rows))


def search_interactions(
    learner_id: str,
    query_embedding: list[float],
    *,
    limit: int = 5,
) -> list[dict]:
    """Return the top-``limit`` interactions closest to ``query_embedding`` by
    cosine distance (pgvector ``<=>`` operator).  Only rows that have a stored
    embedding are considered; rows with NULL embeddings are skipped."""
    emb_str = "[" + ",".join(str(f) for f in query_embedding) + "]"
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_INTERACTION_SELECT} FROM interactions "
                "WHERE learner_id = %s AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s::vector "
                "LIMIT %s",
                (learner_id, emb_str, limit),
            )
            return cur.fetchall()


def recall_context_turns(
    learner_id: str,
    query_embedding: list[float] | None,
    *,
    recent_n: int = 6,
    semantic_k: int = 4,
) -> list[dict]:
    """Hybrid recall: recent turns + top-K semantic matches, merged and
    deduplicated by primary key, then sorted chronologically.

    If ``query_embedding`` is ``None`` (embeddings not available or not yet
    computed), returns only the ``recent_n`` most recent turns so the caller
    always gets *something* useful.
    """
    recent = get_recent_interactions(learner_id, limit=recent_n)
    if query_embedding is None:
        return recent
    semantic = search_interactions(learner_id, query_embedding, limit=semantic_k)
    # Union, deduplicate on id, restore chronological order.
    seen: set[int] = set()
    merged: list[dict] = []
    for row in recent + semantic:
        if row["id"] not in seen:
            seen.add(row["id"])
            merged.append(row)
    merged.sort(key=lambda r: r["created_at"])
    return merged


def get_interactions_page(
    learner_id: str,
    *,
    limit: int = 20,
    before_id: int | None = None,
) -> list[dict]:
    """Return up to ``limit`` interactions for a learner, oldest-first.

    When ``before_id`` is given, only rows with ``id < before_id`` are returned
    (cursor-based pagination — load older pages by passing the smallest ``id``
    already displayed). When ``before_id`` is ``None``, returns the most recent
    ``limit`` rows. Rows are reversed to oldest-first before returning so the
    caller can prepend them to a conversation view directly.
    """
    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if before_id is not None:
                cur.execute(
                    f"SELECT {_INTERACTION_SELECT} FROM interactions "
                    "WHERE learner_id = %s AND id < %s "
                    "ORDER BY id DESC LIMIT %s",
                    (str(learner_id), before_id, limit),
                )
            else:
                cur.execute(
                    f"SELECT {_INTERACTION_SELECT} FROM interactions "
                    "WHERE learner_id = %s "
                    "ORDER BY id DESC LIMIT %s",
                    (str(learner_id), limit),
                )
            rows = cur.fetchall()
            rows.reverse()  # oldest-first for chronological display
            return rows
