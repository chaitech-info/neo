"""Məktub təsdiqi yalnız CEO rolunda: `approver` (Təsdiqləyici) rolu `ceo` (CEO) ilə əvəz olunur,
mövcud təsdiqləyicilər CEO olur. Admin artıq təsdiqləyə bilmir.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

BOTH = ("admin", "approver", "ceo", "manager", "sales", "viewer")
NEW_ROLES = ("admin", "ceo", "manager", "sales", "viewer")
OLD_ROLES = ("admin", "approver", "manager", "sales", "viewer")
NAMING = {"ck": "ck_%(table_name)s_%(constraint_name)s"}
ROLES = sa.table("roles", sa.column("code"), sa.column("name"))
USERS = sa.table("users", sa.column("role_code"))


def _roles_check(values) -> None:
    """roles.code CHECK-i (0012 ilə eyni üsul) yeni dəyər siyahısı ilə."""
    if op.get_bind().dialect.name == "sqlite":
        table = sa.Table(
            "roles", sa.MetaData(naming_convention=NAMING),
            sa.Column("code", sa.Enum(*values, name="role_code", native_enum=False, create_constraint=True, length=32),
                      primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
        )
        with op.batch_alter_table("roles", copy_from=table, recreate="always"):
            pass
        return
    op.drop_constraint(op.f("ck_roles_role_code"), "roles", type_="check")
    op.create_check_constraint(op.f("ck_roles_role_code"), "roles",
                               "code IN (" + ", ".join(f"'{v}'" for v in values) + ")")


def _swap(add: str, add_name: str, drop: str, final) -> None:
    _roles_check(BOTH)
    op.bulk_insert(ROLES, [{"code": add, "name": add_name}])
    op.execute(USERS.update().where(USERS.c.role_code == drop).values(role_code=add))
    op.execute(ROLES.delete().where(ROLES.c.code == drop))
    _roles_check(final)


def upgrade() -> None:
    _swap("ceo", "CEO", "approver", NEW_ROLES)


def downgrade() -> None:
    _swap("approver", "Təsdiqləyici", "ceo", OLD_ROLES)
