"""init

Revision ID: 1a4a0335dda3
Revises:
Create Date: 2026-02-26 14:55:31.521198

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "1a4a0335dda3"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

jobstatus_enum = postgresql.ENUM(
    "pending", "running", "done", "error", "cancelled",
    name="jobstatus",
    create_type=False,
)


def upgrade() -> None:
    """Create all initial tables and enums."""
    jobstatus_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("email", sa.String(), unique=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "translation_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("status", jobstatus_enum, nullable=False),
        sa.Column("source_lang", sa.String(8), nullable=True),
        sa.Column("target_lang", sa.String(8), nullable=False),
        sa.Column("input_path", sa.Text(), nullable=False),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("chunk_total", sa.Integer(), nullable=True),
        sa.Column("chunk_done", sa.Integer(), server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "glossaries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("source_term", sa.Text(), nullable=False),
        sa.Column("target_term", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "source_term", name="uq_glossary_user_term"
        ),
    )

    op.create_table(
        "translation_history",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "job_id",
            sa.UUID(),
            sa.ForeignKey("translation_jobs.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("source_lang", sa.String(8), nullable=True),
        sa.Column("target_lang", sa.String(8), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Drop all initial tables and enums."""
    op.drop_table("translation_history")
    op.drop_table("glossaries")
    op.drop_table("translation_jobs")
    op.drop_table("users")
    jobstatus_enum.drop(op.get_bind(), checkfirst=True)
