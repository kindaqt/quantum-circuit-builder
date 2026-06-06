"""initial memory schema: learners, sessions, interactions, quizzes

Establishes the relational tables behind the tutor's memory features. Embedding
(pgvector) columns for semantic recall are intentionally NOT created here — they
land in a later migration alongside the code that writes them, so the schema
never gets ahead of what uses it.

Revision ID: 0001
Revises:
Create Date: 2026-06-06
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A learner is identified by an opaque UUID the browser stores locally; there
    # is no auth. The onboarding intake fills in level/background/interests/goals.
    op.execute(
        """
        CREATE TABLE learners (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            display_name TEXT,
            level        TEXT,
            background   TEXT,
            interests    TEXT,
            goals        TEXT,
            onboarded_at TIMESTAMPTZ
        );
        """
    )

    # One study session = one continuous sitting with a chosen persona/model.
    op.execute(
        """
        CREATE TABLE learning_sessions (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            learner_id UUID NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
            persona    TEXT,
            provider   TEXT,
            model      TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at   TIMESTAMPTZ
        );
        CREATE INDEX idx_sessions_learner ON learning_sessions (learner_id, started_at DESC);
        """
    )

    # The persisted Q&A turns — the raw material for long-term recall. (The dense
    # embedding column is added in the pgvector migration.)
    op.execute(
        """
        CREATE TABLE interactions (
            id         BIGSERIAL PRIMARY KEY,
            session_id UUID REFERENCES learning_sessions(id) ON DELETE CASCADE,
            learner_id UUID NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
            role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content    TEXT NOT NULL,
            persona    TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_interactions_learner ON interactions (learner_id, created_at DESC);
        """
    )

    # Mini-quizzes for retention tracking: the question asked, the expected
    # answer, what the learner said, and how it was graded (correct + 0..1 score).
    op.execute(
        """
        CREATE TABLE quizzes (
            id             BIGSERIAL PRIMARY KEY,
            learner_id     UUID NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
            session_id     UUID REFERENCES learning_sessions(id) ON DELETE SET NULL,
            topic          TEXT,
            question       TEXT NOT NULL,
            expected_answer TEXT,
            learner_answer TEXT,
            correct        BOOLEAN,
            score          REAL,
            asked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            answered_at    TIMESTAMPTZ
        );
        CREATE INDEX idx_quizzes_learner_topic ON quizzes (learner_id, topic);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quizzes;")
    op.execute("DROP TABLE IF EXISTS interactions;")
    op.execute("DROP TABLE IF EXISTS learning_sessions;")
    op.execute("DROP TABLE IF EXISTS learners;")
