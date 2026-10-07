"""scrape_runs.items_fetched — filtrdən əvvəl saytdan gələn elan sayı.

`items_seen` yalnız filtrdən keçəni sayır: "sayt işləyir, uyğun heç nə yoxdur"
ilə "selector sınıb, heç nə tapılmır" eyni görünürdü (0). Dashboard-dakı mənbə
sağlamlığı bunu ayırmaq üçün işlədir. Köhnə run-larda NULL qalır.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scrape_runs") as batch:
        batch.add_column(sa.Column("items_fetched", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scrape_runs") as batch:
        batch.drop_column("items_fetched")
