"""Add grade (text) and feedback columns to quizzes

The original quizzes table (0001) had a boolean `correct` column; we keep it
for backward-compat but the primary grading field is now `grade` (text:
"correct" / "partial" / "incorrect") paired with a `feedback` explanation from
the model.  score REAL stays and carries 1.0 / 0.5 / 0.0 respectively.

Revision ID: 0002
Revises:     0001
Create Date: 2026-06-06
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS grade    TEXT,
            ADD COLUMN IF NOT EXISTS feedback TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE quizzes
            DROP COLUMN IF EXISTS grade,
            DROP COLUMN IF EXISTS feedback;
        """
    )
