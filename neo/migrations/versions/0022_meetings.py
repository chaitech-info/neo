"""meetings: canlı görüşün qısa xülasəsi (fürsət səhifəsi → "Görüşlər") və opportunities.win_probability.

Hər görüşdən sonra satış əməkdaşı yazır: tarix, kiminlə, xülasə və CRM hesabatlarının dörd ölçüsü —
konversiya (görüşdən əvvəlki → sonrakı mərhələ), sikl (gözlənilən qərar tarixi), itirmə səbəbi
(uduzulubsa) və proqnoz (qazanma ehtimalı %, gözlənilən dəyər). Son proqnoz fürsətdə də saxlanılır.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("met_on", sa.Date(), nullable=False),
        sa.Column("participants", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("stage_before", sa.String(length=16), nullable=True),
        sa.Column("stage_after", sa.String(length=16), nullable=True),
        sa.Column("decision_on", sa.Date(), nullable=True),
        sa.Column("lost_reason_code", sa.String(length=32), nullable=True),
        sa.Column("lost_reason_note", sa.Text(), nullable=True),
        sa.Column("probability", sa.Integer(), nullable=True),
        sa.Column("expected_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("next_step", sa.Text(), nullable=True),
        sa.Column("next_step_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_meetings_opportunity_id", "meetings", ["opportunity_id"])
    with op.batch_alter_table("opportunities") as batch:
        batch.add_column(sa.Column("win_probability", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("opportunities") as batch:
        batch.drop_column("win_probability")
    op.drop_index("ix_meetings_opportunity_id", table_name="meetings")
    op.drop_table("meetings")
