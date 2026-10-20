"""user_keywords — hər istifadəçinin öz açar sözləri (Validasiyada "Mənim sözlərim" filtri, scrape-ə də qoşulur);
lead_briefs — xəbərin AI ilə qısa səsli izahı (mətn + mp3, bir dəfə yaradılır);
outreach.attachments — məktuba əlavə olunmuş faylların adları;
opportunities.leaders_fetched_at — şirkət rəhbərlərinin LinkedIn profilləri axtarılıbmı (Apify, pullu — bir dəfə).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_user_keywords_user_id_users"), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("match", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "term", name="uq_user_keywords_user_id"),
    )
    op.create_index("ix_user_keywords_user_id", "user_keywords", ["user_id"])
    op.create_table(
        "lead_briefs",
        sa.Column("lead_key", sa.String(64), sa.ForeignKey("leads.dedupe_key", name="fk_lead_briefs_lead_key_leads"),
                  primary_key=True),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("audio", sa.LargeBinary(), nullable=True),
        sa.Column("voice", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.add_column("outreach", sa.Column("attachments", sa.Text(), nullable=True))
    op.add_column("opportunities", sa.Column("leaders_fetched_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("opportunities") as batch:
        batch.drop_column("leaders_fetched_at")
    with op.batch_alter_table("outreach") as batch:
        batch.drop_column("attachments")
    op.drop_table("lead_briefs")
    op.drop_table("user_keywords")
