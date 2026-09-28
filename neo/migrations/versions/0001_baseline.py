"""Baseline: CRM-dən əvvəlki yeganə cədvəl `leads` (main @ dd78016).

Mövcud bazalarda bu miqrasiya icra olunmur — `app/db/migrate.py` onu `stamp`
edir (cədvəl artıq var). Yalnız boş bazada cədvəli yaradır.

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("dedupe_key", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("entity_name", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.String(64), nullable=True),
        sa.Column("contact_email", sa.String(128), nullable=True),
        sa.Column("contact_website", sa.Text(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(), nullable=False),
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("ai_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("crm_opportunity_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("leads")
