"""İstifadəçi alətləri və poçt:
- roles: yeni rol `approver` (Təsdiqləyici) — məktubları təsdiqləyir/rədd edir;
- users.lead_filters — Validasiyada A/B/C üçün şəxsi süzgəc; user_categories — öz kateqoriyaları;
- letter_templates — məktub şablonları; letter_approvals — "Təsdiqə göndər" növbəsi;
- mailboxes — göndərən şirkətlərin poçtu; outreach.mailbox_id; inbound_emails — gələn cavablar;
  opportunities.reply_status — gözləmədə / potensial müştəri / imtina;
- reminders, notifications — şəxsi xatırlatmalar və saytdaxili bildirişlər;
- client_contracts — müqaviləsi bitən şirkətlər; employee_searches — LinkedIn işçi siyahısı (keş).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

OLD_ROLES = ("admin", "manager", "sales", "viewer")
NEW_ROLES = ("admin", "approver", "manager", "sales", "viewer")
NAMING = {"ck": "ck_%(table_name)s_%(constraint_name)s"}


def _roles_check(values) -> None:
    """roles.code-un CHECK-i (0002: VARCHAR + CHECK, native enum yoxdur) yeni dəyər siyahısı ilə."""
    if op.get_bind().dialect.name == "sqlite":
        # SQLite CHECK-i dəyişə bilmir — cədvəl yeni tərif ilə nüsxələnib yenidən qurulur.
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


def upgrade() -> None:
    _roles_check(NEW_ROLES)
    op.bulk_insert(sa.table("roles", sa.column("code"), sa.column("name")),
                   [{"code": "approver", "name": "Təsdiqləyici"}])

    op.add_column("users", sa.Column("lead_filters", sa.Text(), nullable=True))

    op.create_table(
        "user_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_user_categories_user_id_users"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("tiers", sa.String(8), nullable=True),
        sa.Column("kinds", sa.String(64), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_categories_user_id", "user_categories", ["user_id"])

    op.create_table(
        "letter_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_letter_templates_user_id_users"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_shared", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_letter_templates_user_id", "letter_templates", ["user_id"])

    op.create_table(
        "mailboxes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("smtp_host", sa.String(255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("imap_host", sa.String(255), nullable=True),
        sa.Column("imap_port", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("key", name="uq_mailboxes_key"),
    )
    with op.batch_alter_table("outreach") as batch:
        batch.add_column(sa.Column("mailbox_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_outreach_mailbox_id_mailboxes", "mailboxes", ["mailbox_id"], ["id"])
    op.add_column("opportunities", sa.Column("reply_status", sa.String(16), nullable=True))
    op.create_index("ix_opportunities_reply_status", "opportunities", ["reply_status"])

    op.create_table(
        "inbound_emails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("mailbox_id", sa.Integer(), sa.ForeignKey("mailboxes.id", name="fk_inbound_emails_mailbox_id_mailboxes"), nullable=True),
        sa.Column("outreach_id", sa.Integer(), sa.ForeignKey("outreach.id", name="fk_inbound_emails_outreach_id_outreach"), nullable=True),
        sa.Column("opportunity_id", sa.Integer(),
                  sa.ForeignKey("opportunities.id", name="fk_inbound_emails_opportunity_id_opportunities"), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sentiment", sa.String(16), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("message_id", name="uq_inbound_emails_message_id"),
    )
    op.create_index("ix_inbound_emails_outreach_id", "inbound_emails", ["outreach_id"])
    op.create_index("ix_inbound_emails_opportunity_id", "inbound_emails", ["opportunity_id"])

    op.create_table(
        "client_contracts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("organizations.id", name="fk_client_contracts_organization_id_organizations"), nullable=True),
        sa.Column("contract_end", sa.Date(), nullable=False),
        sa.Column("contact", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_client_contracts_created_by_id_users"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_client_contracts_contract_end", "client_contracts", ["contract_end"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_reminders_user_id_users"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("remind_before_min", sa.Integer(), nullable=False),
        sa.Column("remind_at", sa.DateTime(), nullable=False),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.Column("contract_id", sa.Integer(),
                  sa.ForeignKey("client_contracts.id", name="fk_reminders_contract_id_client_contracts"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
    op.create_index("ix_reminders_remind_at", "reminders", ["remind_at"])
    op.create_index("ix_reminders_contract_id", "reminders", ["contract_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_notifications_user_id_users"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])

    op.create_table(
        "letter_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(),
                  sa.ForeignKey("opportunities.id", name="fk_letter_approvals_opportunity_id_opportunities"), nullable=False),
        sa.Column("requested_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", name="fk_letter_approvals_requested_by_id_users"), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("language", sa.String(2), nullable=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", name="fk_letter_approvals_contact_id_contacts"), nullable=True),
        sa.Column("mailbox_id", sa.Integer(), sa.ForeignKey("mailboxes.id", name="fk_letter_approvals_mailbox_id_mailboxes"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_letter_approvals_decided_by_id_users"), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_letter_approvals_opportunity_id", "letter_approvals", ["opportunity_id"])
    op.create_index("ix_letter_approvals_requested_by_id", "letter_approvals", ["requested_by_id"])
    op.create_index("ix_letter_approvals_status", "letter_approvals", ["status"])

    op.create_table(
        "employee_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("company_key", sa.String(200), nullable=False),
        sa.Column("items", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_employee_searches_user_id_users"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_employee_searches_company_key", "employee_searches", ["company_key"])


def downgrade() -> None:
    op.drop_table("employee_searches")
    op.drop_table("letter_approvals")
    op.drop_table("notifications")
    op.drop_table("reminders")
    op.drop_table("client_contracts")
    op.drop_table("inbound_emails")
    op.drop_index("ix_opportunities_reply_status", table_name="opportunities")
    with op.batch_alter_table("opportunities") as batch:
        batch.drop_column("reply_status")
    with op.batch_alter_table("outreach") as batch:
        batch.drop_constraint("fk_outreach_mailbox_id_mailboxes", type_="foreignkey")
        batch.drop_column("mailbox_id")
    op.drop_table("mailboxes")
    op.drop_table("letter_templates")
    op.drop_table("user_categories")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("lead_filters")
    # Təsdiqləyicilər menecer olur — rol silinməzdən əvvəl FK pozulmasın.
    op.execute("UPDATE users SET role_code = 'manager' WHERE role_code = 'approver'")
    op.execute("DELETE FROM roles WHERE code = 'approver'")
    _roles_check(OLD_ROLES)
