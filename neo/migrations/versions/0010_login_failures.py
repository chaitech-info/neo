"""login_failures — səhv giriş cəhdləri bazada (əvvəl prosesin yaddaşında idi, Vercel-də işləmirdi);
outreach.send_key — məktubun ikiqat göndərilməsinin qarşısını alan birdəfəlik açar.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_login_failures_email", "login_failures", ["email"])
    op.create_index("ix_login_failures_created_at", "login_failures", ["created_at"])
    op.add_column("outreach", sa.Column("send_key", sa.String(64), nullable=True))
    op.create_index("ix_outreach_send_key", "outreach", ["send_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_outreach_send_key", table_name="outreach")
    with op.batch_alter_table("outreach") as batch:
        batch.drop_column("send_key")
    op.drop_table("login_failures")
