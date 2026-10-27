"""Şirkət bazası, əməkdaşlıq razılaşmaları, saatsız tapşırıqlar:
- tasks.due_at (DateTime, UTC) → tasks.due_on (Date, Bakı tarixi) — tapşırıqlarda saat yoxdur;
- organizations: domain, industry, analysis (son uğurlu analiz JSON), analysis_status, analysed_at,
  analysed_by_id, listed_at (şirkət bazasına düşdüyü vaxt: analiz və ya "Şirkət əlavə et") — "Şirkət bazası";
  notes, notes_updated_at, notes_updated_by_id — şirkət haqqında sərbəst əlavə qeydlər;
- client_contracts → əməkdaşlıq razılaşmaları: title, status, starts_on, updated_at;
  contract_end boş ola bilər (müddətsiz razılaşma).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-27
"""
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

BAKU_OFFSET = timedelta(hours=4)


def _domain(url: str | None) -> str | None:
    url = (url or "").strip().lower()
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else f"http://{url}").hostname or ""
    except ValueError:
        return None
    host = host.removeprefix("www.")
    return host[:255] if "." in host else None


def upgrade() -> None:
    bind = op.get_bind()

    # --- tapşırıqlar: yalnız tarix -------------------------------------------
    op.add_column("tasks", sa.Column("due_on", sa.Date(), nullable=True))
    tasks = sa.table("tasks", sa.column("id", sa.Integer), sa.column("due_at", sa.DateTime),
                     sa.column("due_on", sa.Date))
    for tid, due_at in bind.execute(sa.select(tasks.c.id, tasks.c.due_at).where(tasks.c.due_at.isnot(None))).all():
        if isinstance(due_at, str):  # SQLite köhnə sətirlər
            due_at = datetime.fromisoformat(due_at)
        bind.execute(tasks.update().where(tasks.c.id == tid).values(due_on=(due_at + BAKU_OFFSET).date()))
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("due_at")
    op.create_index("ix_tasks_due_on", "tasks", ["due_on"])

    # --- şirkət bazası --------------------------------------------------------
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("domain", sa.String(255), nullable=True))
        batch.add_column(sa.Column("industry", sa.Text(), nullable=True))
        batch.add_column(sa.Column("analysis", sa.Text(), nullable=True))
        batch.add_column(sa.Column("analysis_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("analysed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("listed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("notes_updated_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("notes_updated_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_organizations_notes_updated_by_id_users", "users", ["notes_updated_by_id"], ["id"])
        batch.add_column(sa.Column("analysed_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_organizations_analysed_by_id_users", "users", ["analysed_by_id"], ["id"])
    op.create_index("ix_organizations_domain", "organizations", ["domain"])
    op.create_index("ix_organizations_analysed_at", "organizations", ["analysed_at"])
    op.create_index("ix_organizations_listed_at", "organizations", ["listed_at"])
    orgs = sa.table("organizations", sa.column("id", sa.Integer), sa.column("website", sa.Text),
                    sa.column("domain", sa.String), sa.column("listed_at", sa.DateTime),
                    sa.column("created_at", sa.DateTime))
    for oid, website in bind.execute(sa.select(orgs.c.id, orgs.c.website).where(orgs.c.website.isnot(None))).all():
        if dom := _domain(website):
            bind.execute(orgs.update().where(orgs.c.id == oid).values(domain=dom))
    # "Şirkət əlavə et"-dən əvvəl əlavə edilmiş şirkətlər şirkət bazasında görünsün.
    cc = sa.table("client_contracts", sa.column("organization_id", sa.Integer))
    bind.execute(orgs.update().where(orgs.c.id.in_(sa.select(cc.c.organization_id).where(cc.c.organization_id.isnot(None))))
                 .values(listed_at=orgs.c.created_at))

    # --- əməkdaşlıq razılaşmaları --------------------------------------------
    with op.batch_alter_table("client_contracts") as batch:
        batch.add_column(sa.Column("title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="active"))
        batch.add_column(sa.Column("starts_on", sa.Date(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch.alter_column("contract_end", existing_type=sa.Date(), nullable=True)
    op.create_index("ix_client_contracts_organization_id", "client_contracts", ["organization_id"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_client_contracts_organization_id", table_name="client_contracts")
    cc = sa.table("client_contracts", sa.column("contract_end", sa.Date))
    bind.execute(cc.delete().where(cc.c.contract_end.is_(None)))  # köhnə sxem bitmə tarixsiz sətir götürmür
    with op.batch_alter_table("client_contracts") as batch:
        batch.alter_column("contract_end", existing_type=sa.Date(), nullable=False)
        batch.drop_column("updated_at")
        batch.drop_column("starts_on")
        batch.drop_column("status")
        batch.drop_column("title")

    op.drop_index("ix_organizations_listed_at", table_name="organizations")
    op.drop_index("ix_organizations_analysed_at", table_name="organizations")
    op.drop_index("ix_organizations_domain", table_name="organizations")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_constraint("fk_organizations_notes_updated_by_id_users", type_="foreignkey")
        batch.drop_column("notes_updated_by_id")
        batch.drop_column("notes_updated_at")
        batch.drop_column("notes")
        batch.drop_constraint("fk_organizations_analysed_by_id_users", type_="foreignkey")
        batch.drop_column("analysed_by_id")
        batch.drop_column("listed_at")
        batch.drop_column("analysed_at")
        batch.drop_column("analysis_status")
        batch.drop_column("analysis")
        batch.drop_column("industry")
        batch.drop_column("domain")

    op.drop_index("ix_tasks_due_on", table_name="tasks")
    op.add_column("tasks", sa.Column("due_at", sa.DateTime(), nullable=True))
    tasks = sa.table("tasks", sa.column("id", sa.Integer), sa.column("due_at", sa.DateTime),
                     sa.column("due_on", sa.Date))
    for tid, due_on in bind.execute(sa.select(tasks.c.id, tasks.c.due_on).where(tasks.c.due_on.isnot(None))).all():
        if isinstance(due_on, str):
            due_on = date.fromisoformat(due_on[:10])
        bind.execute(tasks.update().where(tasks.c.id == tid).values(
            due_at=datetime(due_on.year, due_on.month, due_on.day, 9) - BAKU_OFFSET))
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("due_on")
