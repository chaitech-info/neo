"""offers.language: təklifin dili (az / en / ru) — Alətlər → Qiymət təklifi → "Təklif dili". PDF həmin dildə
qurulur (tarixçədən açanda da); boş — azərbaycanca (köhnə təkliflər).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.add_column(sa.Column("language", sa.String(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.drop_column("language")
