"""Tests for the optional Postgres layer.

Two groups:

* Graceful-degradation tests run everywhere (no database needed) and pin down
  the contract that the core app keeps working when the DB is absent.
* Live tests round-trip real rows and are skipped unless a migrated database is
  reachable (set QCB_DATABASE_URL, `make db-up && make migrate`).
"""
import uuid

import pytest

import db


# ---- Graceful degradation (no database required) ---------------------------
def test_unconfigured_reports_unavailable_without_raising(monkeypatch):
    monkeypatch.delenv("QCB_DATABASE_URL", raising=False)
    assert db.configured() is False
    assert db.available() is False
    # healthy() must never raise — it turns every failure into a clean False.
    assert db.healthy() is False


def test_connection_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("QCB_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        with db.connection():
            pass


def test_status_has_expected_shape(monkeypatch):
    monkeypatch.delenv("QCB_DATABASE_URL", raising=False)
    s = db.status()
    assert set(s) == {"driver", "configured", "healthy"}
    assert s["configured"] is False
    assert s["healthy"] is False


def test_health_endpoint_ok_without_db(client, monkeypatch):
    monkeypatch.delenv("QCB_DATABASE_URL", raising=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"]["configured"] is False
    assert body["db"]["healthy"] is False


# ---- Live database (skipped unless a migrated DB is reachable) -------------
live_db = pytest.mark.skipif(
    not db.healthy(),
    reason="no reachable migrated database (set QCB_DATABASE_URL, run `make db-up && make migrate`)",
)


@live_db
def test_health_endpoint_reports_healthy_when_up(client):
    body = client.get("/health").json()
    assert body["db"]["healthy"] is True


@live_db
def test_schema_round_trips_a_learner_and_cascades():
    marker = f"pytest-{uuid.uuid4()}"
    with db.connection() as conn:
        learner_id = conn.execute(
            "INSERT INTO learners (display_name, level) VALUES (%s, %s) RETURNING id",
            (marker, "beginner"),
        ).fetchone()[0]
        session_id = conn.execute(
            "INSERT INTO learning_sessions (learner_id, persona) VALUES (%s, %s) RETURNING id",
            (learner_id, "professor"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO interactions (session_id, learner_id, role, content) VALUES (%s, %s, %s, %s)",
            (session_id, learner_id, "user", "what is a qubit?"),
        )
        conn.execute(
            "INSERT INTO quizzes (learner_id, session_id, question, correct, score) "
            "VALUES (%s, %s, %s, %s, %s)",
            (learner_id, session_id, "Define superposition.", True, 1.0),
        )

        # Deleting the learner cascades to sessions, interactions, and quizzes.
        conn.execute("DELETE FROM learners WHERE id = %s", (learner_id,))
        for table in ("learning_sessions", "interactions", "quizzes"):
            remaining = conn.execute(
                f"SELECT count(*) FROM {table} WHERE learner_id = %s", (learner_id,)
            ).fetchone()[0]
            assert remaining == 0
