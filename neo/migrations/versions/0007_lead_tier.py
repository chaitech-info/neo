"""leads.tier — A/B/C kommersiya kateqoriyası (v2 qiymətləndirmə, app/ai_scoring.py).

Əvvəl kateqoriya yalnız `ai_reason`-un "[A] " prefiksində idi — paneldə süzmək və
sıralamaq olmurdu. Mövcud lead-lər prefiksdən doldurulur, prefiks səbəbdən silinir.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("tier", sa.String(1), nullable=True))
    op.create_index("ix_leads_tier", "leads", ["tier"])
    conn = op.get_bind()
    for tier in ("A", "B", "C"):
        prefix = f"[{tier}] "
        rows = conn.execute(sa.text("SELECT dedupe_key, ai_reason FROM leads WHERE ai_reason LIKE :p"),
                            {"p": prefix + "%"}).all()
        for key, reason in rows:
            conn.execute(sa.text("UPDATE leads SET tier = :t, ai_reason = :r WHERE dedupe_key = :k"),
                         {"t": tier, "r": reason[len(prefix):], "k": key})


def downgrade() -> None:
    op.drop_index("ix_leads_tier", table_name="leads")
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("tier")
