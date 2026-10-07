"""triage_cache — başlıq süzgəcinin qərarları bazada (prosesdən asılı olmayaraq).

Modal hər run üçün təzə konteyner açır — yaddaşdakı keş hər dəfə boş idi və
saatlıq xəbər mənbələri eyni 25-60 başlığı təkrar AI-yə göndərirdi (Phase 2:
9 yeni RSS mənbəyi ilə ~25M token/ay). Açar = sha1(növ | prompt versiyası | başlıq).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "triage_cache",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("fit", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_triage_cache_created_at", "triage_cache", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_triage_cache_created_at", table_name="triage_cache")
    op.drop_table("triage_cache")
