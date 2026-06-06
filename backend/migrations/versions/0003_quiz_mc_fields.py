"""Add multiple-choice fields to quizzes table

`type`          TEXT DEFAULT 'text' — 'text' (open-ended) or 'multiple_choice'.
`options`       JSONB — for MC: array of 4 option strings (indices map to A/B/C/D).
`correct_option` TEXT — for MC: letter of the correct option ('A'–'D').

Revision ID: 0003
Revises:     0002
Create Date: 2026-06-06
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS type            TEXT    NOT NULL DEFAULT 'text',
            ADD COLUMN IF NOT EXISTS options         JSONB,
            ADD COLUMN IF NOT EXISTS correct_option  TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE quizzes
            DROP COLUMN IF EXISTS type,
            DROP COLUMN IF EXISTS options,
            DROP COLUMN IF EXISTS correct_option;
        """
    )
