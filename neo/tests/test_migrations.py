"""Alembic miqrasiyaları: boş baza, köhnə (CRM-dən əvvəlki) baza, drift, geri qaytarma."""
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import alembic_config, upgrade_to_head
from app.db.models import Base
from app.scrapers.base import NEWS_KEYWORDS, TENDER_KEYWORDS

CRM_TABLES = {
    "roles", "users", "sources", "scrape_runs", "interest_areas", "keywords",
    "organizations", "tenders", "opportunities", "tasks", "lead_events", "contacts", "outreach",
}
NEW_LEAD_COLUMNS = {
    "deadline_at", "organization_id", "tender_id", "duplicate_of", "ai_model", "reviewed_by_id", "reviewed_at",
}

# main @ dd78016-dakı `create_all()`-un yaratdığı cədvəl (CRM-dən əvvəl).
LEGACY_LEADS_DDL = """
CREATE TABLE leads (
    dedupe_key VARCHAR(64) NOT NULL PRIMARY KEY, source VARCHAR(32) NOT NULL, source_url TEXT NOT NULL,
    title TEXT NOT NULL, raw_text TEXT NOT NULL, entity_name TEXT, contact_phone VARCHAR(64),
    contact_email VARCHAR(128), contact_website TEXT, category VARCHAR(32) NOT NULL, location VARCHAR(128),
    posted_at DATETIME, scraped_at DATETIME NOT NULL, ai_score FLOAT, ai_reason TEXT,
    status VARCHAR(16) NOT NULL, crm_opportunity_id VARCHAR(64)
)"""


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    yield eng
    eng.dispose()


def _version(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_fresh_database_gets_full_schema_and_seed(engine):
    upgrade_to_head(engine)

    tables = set(inspect(engine).get_table_names())
    assert CRM_TABLES | {"leads", "alembic_version"} <= tables
    assert _version(engine) == "0012"
    assert "items_fetched" in {c["name"] for c in inspect(engine).get_columns("scrape_runs")}
    with engine.connect() as c:
        assert c.execute(text("SELECT code FROM roles ORDER BY code")).scalars().all() == [
            "admin", "approver", "manager", "sales", "viewer"]
        assert c.execute(text("SELECT count(*) FROM sources WHERE is_active")).scalar() == len(__import__("app.sources", fromlist=["SOURCES"]).SOURCES)
        assert c.execute(text("SELECT is_active FROM sources WHERE key='azerweb'")).scalar() == 0
        assert c.execute(text("SELECT respect_robots FROM sources WHERE key='linkedin'")).scalar() == 0


def test_legacy_database_is_upgraded_without_data_loss(engine):
    with engine.begin() as c:
        c.execute(text(LEGACY_LEADS_DDL))
        c.execute(text(
            "INSERT INTO leads (dedupe_key, source, source_url, title, raw_text, category, scraped_at, status) VALUES "
            "('k1','etender','https://e/1','Keytrinq xidməti','...','tender','2026-09-23 10:00:00','pending_review'),"
            "('k2','oldsource','https://o/2','Köhnə','...','other','2026-09-20 10:00:00','sent_to_crm')"))

    upgrade_to_head(engine)
    upgrade_to_head(engine)  # ikinci dəfə — heç nə etməməlidir

    assert _version(engine) == "0012"
    cols = {c["name"] for c in inspect(engine).get_columns("leads")}
    assert NEW_LEAD_COLUMNS <= cols
    with engine.connect() as c:
        rows = c.execute(text("SELECT dedupe_key, title, status, deadline_at FROM leads ORDER BY dedupe_key")).all()
        assert rows == [("k1", "Keytrinq xidməti", "pending_review", None), ("k2", "Köhnə", "sent_to_crm", None)]
        # Siyahıda olmayan köhnə mənbə itmir — deaktiv mənbə kimi bərpa olunur
        assert c.execute(text("SELECT is_active FROM sources WHERE key='oldsource'")).scalar() == 0


def test_models_match_migrations(engine):
    """Model dəyişib miqrasiya yazılmayıbsa (və ya əksinə) — bu test düşür."""
    upgrade_to_head(engine)
    with engine.connect() as c:
        ctx = MigrationContext.configure(c, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_seeded_keywords_reproduce_code_lists(engine):
    upgrade_to_head(engine)
    with engine.connect() as c:
        rows = c.execute(text("SELECT term, applies_to FROM keywords WHERE NOT is_negative")).all()
        negative = c.execute(text("SELECT term FROM keywords WHERE is_negative")).scalars().all()
    tender = {t for t, scope in rows if scope in ("all", "tender")}
    news = {t for t, scope in rows if scope in ("all", "news")}
    assert tender == set(TENDER_KEYWORDS)
    assert news == set(NEWS_KEYWORDS)
    assert negative == ["mətbəə"]  # 0005: çap tenderləri


def test_0012_approver_role_passes_check_and_downgrades(engine):
    """roles.code CHECK-i yeni rolu qəbul edir; geri qaytaranda təsdiqləyici menecer olur."""
    upgrade_to_head(engine)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO users (id, email, full_name, password_hash, role_code, is_active, created_at) "
            "VALUES (1, 'a@x.az', 'A', 'h', 'approver', 1, '2026-10-21 00:00:00')"))
        with pytest.raises(Exception):  # CHECK hələ də işləyir
            with c.begin_nested():
                c.execute(text("INSERT INTO roles (code, name) VALUES ('boss', 'X')"))
        command.downgrade(alembic_config(c), "0011")
    with engine.connect() as c:
        assert c.execute(text("SELECT role_code FROM users")).scalar() == "manager"
        assert "approver" not in c.execute(text("SELECT code FROM roles")).scalars().all()


def test_downgrade_to_baseline_keeps_leads(engine):
    upgrade_to_head(engine)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO leads (dedupe_key, source, source_url, title, raw_text, category, scraped_at, status) "
            "VALUES ('k1','oxu','https://oxu/1','T','x','event','2026-09-23 10:00:00','new')"))
        command.downgrade(alembic_config(c), "0001")

    assert set(inspect(engine).get_table_names()) == {"leads", "alembic_version"}
    with engine.connect() as c:
        assert c.execute(text("SELECT title FROM leads")).scalar() == "T"


def test_tier_backfilled_from_reason_prefix(engine):
    with engine.begin() as c:
        command.upgrade(alembic_config(c), "0006")
        c.execute(text(
            "INSERT INTO leads (dedupe_key, source, source_url, title, raw_text, category, scraped_at, status, ai_reason) "
            "VALUES ('k1','boss','https://b/1','T','x','corporate','2026-10-05 10:00:00','pending_review','[B] Zavod nahar verir')"))
    upgrade_to_head(engine)
    with engine.connect() as c:
        assert c.execute(text("SELECT tier, ai_reason FROM leads")).one() == ("B", "Zavod nahar verir")


def test_0008_backfills_rejected_by_and_linkedin_post_link(engine):
    with engine.begin() as c:
        command.upgrade(alembic_config(c), "0007")
        c.execute(text(
            "INSERT INTO users (id, email, full_name, password_hash, role_code, is_active, created_at) "
            "VALUES (1, 'a@x.az', 'A', 'h', 'admin', 1, '2026-10-01 00:00:00')"))
        c.execute(text(
            "INSERT INTO leads (dedupe_key, source, source_url, title, raw_text, category, scraped_at, status, reviewed_by_id) "
            "VALUES ('ai','oxu','https://o/1','T','x','event','2026-10-05 10:00:00','rejected',NULL),"
            "('hu','oxu','https://o/2','T','x','event','2026-10-05 10:00:00','rejected',1),"
            "('li','linkedin','https://vumes.az','T','[SİQNAL: nahar]\nElan: https://www.linkedin.com/posts/x-123\n\nmətn',"
            "'corporate','2026-10-05 10:00:00','pending_review',NULL)"))
    upgrade_to_head(engine)
    with engine.connect() as c:
        rows = dict(c.execute(text("SELECT dedupe_key, rejected_by FROM leads")).all())
        origin = c.execute(text("SELECT origin_url FROM leads WHERE dedupe_key='li'")).scalar()
    assert rows == {"ai": "ai", "hu": "human", "li": None}
    assert origin == "https://www.linkedin.com/posts/x-123"
