"""offers.unit_price_override: sorğu mətnində istənən 1 nəfərlik qiymət ("8 yox 5 azn et", app/offer_price.py).
Agent qiyməti paketdən götürür; bu sütun doludursa təklif və PDF həmin qiymətlə göstərilir (tarixçədən açanda da).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.add_column(sa.Column("unit_price_override", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("offers") as batch:
        batch.drop_column("unit_price_override")
