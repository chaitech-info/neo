"""offers.menu_exclude: sorğu mətnində menyudan çıxarılması istənənlər ("menyudan içkini çıxart",
app/offer_price.py) — JSON {"categories": ["İçki"], "dishes": ["..."]}. Agent menyunu paketdən götürür; bu sütun
doludursa PDF həmin kateqoriyalar / yeməklər olmadan qurulur (tarixçədən açanda da).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.add_column(sa.Column("menu_exclude", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.drop_column("menu_exclude")
