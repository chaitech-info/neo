"""Rədd tarixçəsi, LinkedIn linkləri, kontaktlar, ünsiyyət (LinkedIn şərhi / email).

- `leads`: `origin_url` (əsl post/elan linki — LinkedIn işəgötürən lead-ində source_url
  şirkət saytıdır), LinkedIn müəllifi (`author_*`), `rejected_by` (ai/human).
- `lead_events`: təsdiq/rədd/geri qaytarma tarixçəsi.
- `contacts`, `outreach`: kontakt şəxslər və göndərilmiş şərh/məktublar.

Mövcud data: LinkedIn işəgötürən lead-lərinin post linki mətndəki "Elan: <url>"
sətrindən `origin_url`-a; rədd edilmiş lead-lər reviewed_by_id-yə görə ai/human.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

ELAN_RE = re.compile(r"^Elan: (\S+)", re.M)


def _enum(name, *values):
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("origin_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("author_name", sa.Text(), nullable=True))
        batch.add_column(sa.Column("author_title", sa.Text(), nullable=True))
        batch.add_column(sa.Column("author_url", sa.Text(), nullable=True))
        batch.add_column(sa.Column("rejected_by", sa.String(8), nullable=True))

    op.create_table(
        "lead_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_key", sa.String(64), sa.ForeignKey("leads.dedupe_key"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", _enum("lead_action", "approve", "reject", "restore", "comment", "email"), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lead_events_lead_key", "lead_events", ["lead_key"])

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("lead_key", sa.String(64), sa.ForeignKey("leads.dedupe_key"), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("source", _enum("contact_source", "text", "ai", "author", "website", "enrichment", "manual"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_contacts_organization_id", "contacts", ["organization_id"])
    op.create_index("ix_contacts_lead_key", "contacts", ["lead_key"])

    op.create_table(
        "outreach",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", _enum("outreach_kind", "linkedin_comment", "email"), nullable=False),
        sa.Column("lead_key", sa.String(64), sa.ForeignKey("leads.dedupe_key"), nullable=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id"), nullable=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=True),
        sa.Column("to_address", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("language", sa.String(2), nullable=True),
        sa.Column("status", _enum("outreach_status", "sent", "failed", "skipped"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_outreach_lead_key", "outreach", ["lead_key"])
    op.create_index("ix_outreach_opportunity_id", "outreach", ["opportunity_id"])

    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE leads SET rejected_by = CASE WHEN reviewed_by_id IS NULL THEN 'ai' ELSE 'human' END "
        "WHERE status = 'rejected'"))
    rows = conn.execute(sa.text(
        "SELECT dedupe_key, raw_text FROM leads WHERE source = 'linkedin' AND raw_text LIKE '%Elan: %'")).all()
    for key, raw in rows:
        found = ELAN_RE.search(raw or "")
        if found:
            conn.execute(sa.text("UPDATE leads SET origin_url = :u WHERE dedupe_key = :k"),
                         {"u": found.group(1), "k": key})


def downgrade() -> None:
    op.drop_index("ix_outreach_opportunity_id", table_name="outreach")
    op.drop_index("ix_outreach_lead_key", table_name="outreach")
    op.drop_table("outreach")
    op.drop_index("ix_contacts_lead_key", table_name="contacts")
    op.drop_index("ix_contacts_organization_id", table_name="contacts")
    op.drop_table("contacts")
    op.drop_index("ix_lead_events_lead_key", table_name="lead_events")
    op.drop_table("lead_events")
    with op.batch_alter_table("leads") as batch:
        for col in ("rejected_by", "author_url", "author_title", "author_name", "origin_url"):
            batch.drop_column(col)
