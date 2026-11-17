"""Alt şirkətlər: organizations.parent_id — ana şirkət (AI internetdə və saytda tapır, app/subsidiaries.py);
parent_relation / parent_source — münasibət ("100% törəmə", "holdinqin üzvü") və onu göstərən mənbə linki;
subsidiaries_checked_at — ana şirkət üçün alt şirkətlər nə vaxt axtarılıb (boşdursa axtarılmayıb).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_organizations_parent_id_organizations", "organizations", ["parent_id"], ["id"])
        batch.add_column(sa.Column("parent_relation", sa.Text(), nullable=True))
        batch.add_column(sa.Column("parent_source", sa.Text(), nullable=True))
        batch.add_column(sa.Column("subsidiaries_checked_at", sa.DateTime(), nullable=True))
    op.create_index("ix_organizations_parent_id", "organizations", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_organizations_parent_id", table_name="organizations")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_constraint("fk_organizations_parent_id_organizations", type_="foreignkey")
        batch.drop_column("subsidiaries_checked_at")
        batch.drop_column("parent_source")
        batch.drop_column("parent_relation")
        batch.drop_column("parent_id")
