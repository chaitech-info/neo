"""CRM sxemi: toplama, lead və CRM qatları + ilkin data.

Sxem layihəsi: "Neo CRM Sxemi" (razılaşdırılıb 28.09.2026). `leads`-dən heç
nə silinmir — yalnız nullable sütunlar və FK-lar əlavə olunur. İlkin data
(mənbələr, açar sözlər) koddakı siyahıların 28.09.2026 nüsxəsidir; miqrasiya
koddan import ETMİR ki, kod dəyişəndə köhnə miqrasiya eyni nəticəni versin.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _enum(name, *values):
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


# --- İlkin data -------------------------------------------------------------

ROLES = [
    ("admin", "Administrator"),
    ("manager", "Menecer"),
    ("sales", "Satış"),
    ("viewer", "Müşahidəçi"),
]

# (key, name, kind, base_url, schedule_cron, respect_robots, is_active, notes)
# Cədvəllər app/scheduler.py-dəki Celery Beat qeydlərindəndir (Bakı vaxtı).
SOURCES = [
    ("marja", "Marja.az", "news", "https://marja.az", "0 * * * *", True, True, None),
    ("1news", "1news.az", "news", "https://1news.az", "0 * * * *", True, True, None),
    ("oxu", "Oxu.az", "news", "https://oxu.az", "20 * * * *", True, True, None),
    ("fed", "Fed.az", "news", "https://fed.az", "40 * * * *", True, True, None),
    ("fed_tenders", "Fed.az Tenderlər", "tender_portal", "https://fed.az/az/tenderler", "50 */2 * * *", True, True, None),
    ("report", "Report.az Sənaye", "news", "https://report.az", "25 */2 * * *", True, True, None),
    ("etender", "Etender.gov.az", "tender_portal", "https://etender.gov.az", "0 6 * * *", True, True, None),
    ("emlak", "Emlak.gov.az", "news", "https://emlak.gov.az", "30 6 * * *", True, True,
     "Öz açar söz siyahısı var (app/scrapers/emlak.py): hərrac, müsabiqə, obyekt"),
    ("corporate", "Şirkət/agentlik tenderləri", "corporate", None, "10 8,16 * * *", True, True,
     "tourism.gov.az, Rabitəbank, VTB, Azərişıq, Azərenerji (app/scrapers/corporate.py)"),
    ("worldbank", "Dünya Bankı", "intl_api", "https://search.worldbank.org", "0 7 * * *", True, True, None),
    ("bp_edp", "bp EDP", "tender_portal", "https://edp.az", "15 7 * * *", True, True, None),
    ("boss", "boss.az — işçilərinə nahar verən şirkətlər", "jobs", "https://boss.az", "45 7 * * *", True, True, None),
    ("linkedin", "LinkedIn (Apify)", "social", "https://www.linkedin.com", "0 7 * * *", False, True,
     "Lisenziyalı Apify API — robots.txt yox, Apify ToS tətbiq olunur; APIFY_TOKEN lazımdır"),
    ("azerweb", "Azerweb.az", "tender_portal", "https://azerweb.az", None, True, False,
     "Deaktiv: robots.txt `Disallow: /api/`, məzmun yalnız API-dən gəlir (2026-09-23)"),
]

INTEREST_AREAS = [
    # (code, name, priority, ai_description)
    ("staff_meals", "İşçi qidalanması", 1,
     "Əsas məhsul: şirkətlərin əməkdaşları üçün gündəlik hazır yemək — paketlənmiş/porsiyalı "
     "nahar, toplu çatdırılma. Müştərilər: ofislər, zavodlar, tikinti obyektləri, dövlət qurumları."),
    ("events", "Korporativ tədbirlər", 2,
     "İkinci dərəcəli: korporativ tədbir, banket, konfrans və mərasim yeməkləri."),
    ("expansion", "Genişlənmə siqnalları", 2,
     "Dolayı siqnal: çox işçili yeni zavod, ofis, filial və ya tikinti layihəsinin açılması."),
]

# Koddakı siyahılar (app/scrapers/base.py, 28.09.2026) və harada işləndikləri:
#   MEAL_KEYWORDS      → həm xəbər, həm tender        → applies_to = all
#   EVENT_KEYWORDS     → yalnız TENDER_KEYWORDS-də     → applies_to = tender
#   EXPANSION_KEYWORDS → yalnız NEWS_KEYWORDS-də       → applies_to = news
#   "korporativ tədbir/gecə" → NEWS_KEYWORDS əlavəsi  → events, news
MEAL = [
    "keytrinq", "keytring", "keyterinq", "katerinq", "katering", "catering", "iaşə", "qidalanma",
    "qida məhsul", "ərzaq", "yemək", "nahar", "səhər yeməy", "şam yeməy", "bufet", "banket",
    "ziyafət", "fürşet", "furşet", "kofe-brek", "kofe brek", "qəhvə fasil", "çay süfrə", "süfrə",
    "qonaqlıq", "qonaqlama", "питани", "кейтеринг", "обед", "столов", "банкет", "фуршет",
    "продовольств", "food", "meal", "lunch", "canteen", "cafeteria", "coffee break", "banquet",
]
EVENT = [
    "tədbir", "konfrans", "forum", "festival", "mərasim", "bayram", "yubiley", "komandaqurma",
    "teambuilding", "team building", "qəbul", "sərgi", "seminar", "təlim", "yarış", "turnir",
    "çempionat", "olimpiada", "konqres", "sammit", "iftar", "novruz", "mükafatlandırma", "düşərgə",
    "мероприяти", "конференц", "форум", "праздн", "event", "conference", "reception", "ceremony",
    "hospitality", "workshop",
]
EXPANSION = [
    "zavod", "fabrik", "istehsal müəssisə", "sənaye park", "sənaye zona", "texnopark",
    "təməli qoyul", "yeni iş yeri", "iş yeri yaradıl", "iş yeri açıl", "işə götür", "işçi qəbul",
    "ofis açıl", "yeni ofis", "filial açıl", "logistika mərkəz", "anbar kompleks", "qızıl mədən",
    "mis mədən", "filiz", "yatağında istehsal", "istehsala başla", "istehsalına başla",
    "istehsalın başla", "istifadəyə veril", "tikiləcək", "inşa ediləcək",
]
NEWS_EVENT_EXTRA = ["korporativ tədbir", "korporativ gecə"]
ENGLISH = {
    "catering", "food", "meal", "lunch", "canteen", "cafeteria", "coffee break", "banquet",
    "teambuilding", "team building", "event", "conference", "reception", "ceremony",
    "hospitality", "workshop",
}


def _language(term: str) -> str:
    if any("а" <= ch <= "я" for ch in term):
        return "ru"
    return "en" if term in ENGLISH else "az"


def _keyword_rows():
    rows = []
    for area_id, terms, scope in (
        (1, MEAL, "all"),
        (2, EVENT, "tender"),
        (2, NEWS_EVENT_EXTRA, "news"),
        (3, EXPANSION, "news"),
    ):
        for term in terms:
            rows.append({
                "interest_area_id": area_id, "term": term, "language": _language(term),
                "match": "phrase" if " " in term else "prefix", "applies_to": scope,
                "is_negative": False, "is_active": True,
            })
    return rows


# --- Sxem -------------------------------------------------------------------


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("code", _enum("role_code", "admin", "manager", "sales", "viewer"), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role_code", sa.String(32), sa.ForeignKey("roles.code"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "sources",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", _enum("source_kind", "tender_portal", "news", "jobs", "corporate", "intl_api", "social"), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("schedule_cron", sa.String(64), nullable=True),
        sa.Column("respect_robots", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(32), sa.ForeignKey("sources.key"), nullable=False, index=True),
        sa.Column("trigger", _enum("run_trigger", "schedule", "manual", "scrape_all"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", _enum("run_status", "running", "ok", "failed", "blocked_robots"), nullable=False),
        sa.Column("items_seen", sa.Integer(), nullable=False),
        sa.Column("leads_new", sa.Integer(), nullable=False),
        sa.Column("leads_duplicate", sa.Integer(), nullable=False),
        sa.Column("leads_pending", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_table(
        "interest_areas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ai_description", sa.Text(), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("interest_area_id", sa.Integer(), sa.ForeignKey("interest_areas.id"), nullable=False, index=True),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("language", _enum("keyword_language", "az", "en", "ru"), nullable=False),
        sa.Column("match", _enum("keyword_match", "prefix", "word", "phrase"), nullable=False),
        sa.Column("applies_to", _enum("keyword_scope", "all", "news", "tender", "jobs"), nullable=False),
        sa.Column("is_negative", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("interest_area_id", "term", "applies_to"),
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=False, index=True),
        sa.Column("voen", sa.String(10), nullable=True, unique=True),
        sa.Column("kind", _enum("org_kind", "state", "state_company", "private", "intl_org"), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "tenders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True, index=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("reference_no", sa.String(64), nullable=True, index=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("budget_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("status", _enum("tender_status", "open", "closed", "awarded", "cancelled"), nullable=False),
        sa.Column("awarded_to_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    # --- İlkin data (leads FK-sından ƏVVƏL: mövcud source dəyərləri mövcud olmalıdır)
    op.bulk_insert(sa.table("roles", sa.column("code"), sa.column("name")),
                   [{"code": c, "name": n} for c, n in ROLES])
    sources_t = sa.table(
        "sources", sa.column("key"), sa.column("name"), sa.column("kind"), sa.column("base_url"),
        sa.column("schedule_cron"), sa.column("respect_robots", sa.Boolean), sa.column("is_active", sa.Boolean),
        sa.column("notes"),
    )
    known = {s[0] for s in SOURCES}
    op.bulk_insert(sources_t, [
        dict(zip(("key", "name", "kind", "base_url", "schedule_cron", "respect_robots", "is_active", "notes"), s))
        for s in SOURCES
    ])
    # Bazada siyahıda olmayan köhnə mənbə adı varsa (məs. lokal sınaqdan) — itirmirik,
    # deaktiv mənbə kimi əlavə edirik ki, FK pozulmasın.
    bind = op.get_bind()
    orphans = [r[0] for r in bind.execute(sa.text("SELECT DISTINCT source FROM leads")) if r[0] not in known]
    if orphans:
        op.bulk_insert(sources_t, [
            {"key": k, "name": k, "kind": "news", "base_url": None, "schedule_cron": None,
             "respect_robots": True, "is_active": False, "notes": "Köhnə lead-lərdən bərpa olundu (0002)"}
            for k in orphans
        ])
    op.bulk_insert(
        sa.table("interest_areas", sa.column("id"), sa.column("code"), sa.column("name"),
                 sa.column("ai_description"), sa.column("priority"), sa.column("is_active", sa.Boolean)),
        [{"id": i, "code": c, "name": n, "priority": p, "ai_description": d, "is_active": True}
         for i, (c, n, p, d) in enumerate(INTEREST_AREAS, start=1)],
    )
    op.bulk_insert(
        sa.table("keywords", sa.column("interest_area_id"), sa.column("term"), sa.column("language"),
                 sa.column("match"), sa.column("applies_to"), sa.column("is_negative", sa.Boolean),
                 sa.column("is_active", sa.Boolean)),
        _keyword_rows(),
    )

    # --- leads: yalnız əlavə (SQLite-da batch cədvəli nüsxələyib yenidən qurur, data qalır)
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("deadline_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("tender_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("duplicate_of", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ai_model", sa.String(32), nullable=True))
        batch.add_column(sa.Column("reviewed_by_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_leads_tender_id", ["tender_id"])
        batch.create_foreign_key("fk_leads_source_sources", "sources", ["source"], ["key"])
        batch.create_foreign_key("fk_leads_organization_id_organizations", "organizations", ["organization_id"], ["id"])
        batch.create_foreign_key("fk_leads_tender_id_tenders", "tenders", ["tender_id"], ["id"])
        batch.create_foreign_key("fk_leads_duplicate_of_leads", "leads", ["duplicate_of"], ["dedupe_key"])
        batch.create_foreign_key("fk_leads_reviewed_by_id_users", "users", ["reviewed_by_id"], ["id"])

    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("lead_key", sa.String(64), sa.ForeignKey("leads.dedupe_key"), nullable=True, unique=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True, index=True),
        sa.Column("tender_id", sa.Integer(), sa.ForeignKey("tenders.id"), nullable=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("stage", _enum("opportunity_stage", "new", "contacted", "qualified", "proposal",
                                 "negotiation", "won", "lost"), nullable=False),
        sa.Column("expected_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("expected_close_on", sa.Date(), nullable=True),
        sa.Column("lost_reason_code", _enum("lost_reason", "price", "competitor", "no_response",
                                            "not_eligible", "deadline_missed", "other"), nullable=True),
        sa.Column("lost_reason_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id"), nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("status", _enum("task_status", "open", "in_progress", "done", "cancelled"), nullable=False),
        sa.Column("priority", _enum("task_priority", "low", "normal", "high"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("tasks")
    op.drop_table("opportunities")
    with op.batch_alter_table("leads") as batch:
        for fk in ("fk_leads_reviewed_by_id_users", "fk_leads_duplicate_of_leads", "fk_leads_tender_id_tenders",
                   "fk_leads_organization_id_organizations", "fk_leads_source_sources"):
            batch.drop_constraint(fk, type_="foreignkey")
        batch.drop_index("ix_leads_tender_id")
        for col in ("reviewed_at", "reviewed_by_id", "ai_model", "duplicate_of", "tender_id",
                    "organization_id", "deadline_at"):
            batch.drop_column(col)
    for table in ("tenders", "organizations", "keywords", "interest_areas", "scrape_runs", "sources", "users", "roles"):
        op.drop_table(table)
