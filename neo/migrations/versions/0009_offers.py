"""offers — qiymət təklifi alətinin metadata cədvəli (PDF saxlanmır, agent yenidən yaradır).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offer_id", sa.String(64), nullable=False),
        sa.Column("brand", sa.String(16), nullable=False),
        sa.Column("customer_name", sa.Text(), nullable=True),
        sa.Column("total", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("sample", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_offers_created_by_id_users"), nullable=True),
        sa.Column("lead_id", sa.String(64), sa.ForeignKey("leads.dedupe_key", name="fk_offers_lead_id_leads"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("offer_id", name="uq_offers_offer_id"),
    )
    op.create_index("ix_offers_created_by_id", "offers", ["created_by_id"])
    op.create_index("ix_offers_lead_id", "offers", ["lead_id"])
    op.create_index("ix_offers_created_at", "offers", ["created_at"])


def downgrade() -> None:
    op.drop_table("offers")
