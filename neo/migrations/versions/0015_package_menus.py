"""Paketin menyu planı (Alətlər → Paketlər → Menyu planı):
- package_menus: hansı yemək hansı gün / yemək vaxtı / kateqoriyada verilir, əlavə sahələr (porsiya, kalori …).
  JSON kimi saxlanılır (app/package_menus.py yoxlayır). Agentə paketin mətn menyusu bu plandan yaradılıb göndərilir —
  agent sxemi dəyişmir.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "package_menus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand", sa.String(16), nullable=False),
        sa.Column("package_id", sa.String(64), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_package_menus_updated_by_id_users"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("brand", "package_id", name="uq_package_menus_brand_package"),
    )


def downgrade() -> None:
    op.drop_table("package_menus")
