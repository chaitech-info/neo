"""Yeni mənbələr: media/assosiasiya/sərgi saytları, UNGM+EBRD, jobsearch.az.

`sources` cədvəlinə 3 sətir əlavə olunur (leads.source FK-sı bunlara istinad edəcək);
corporate mənbəsinin qeydi yeni saytlarla (SOCAR) yenilənir.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# (key, name, kind, base_url, schedule_cron, respect_robots, is_active, notes)
NEW_SOURCES = [
    ("media", "Media, assosiasiya və sərgilər", "news", None, "35 9,15 * * *", True, True,
     "APA, AZƏRTAC, BusinessTime, Banker, Trend, AFEZ, İqtisadiyyat Nazirliyi, AZPROMO, TƏBİB, ASK, TÜİB, "
     "Caspian Oil & Gas, BakuBuild, TransLogistica, Rebuild Karabakh (app/scrapers/media.py)"),
    ("intl_tender", "UNGM + EBRD tenderləri", "intl_api", None, "30 7 * * *", True, True,
     "UNGM (ölkə=Azərbaycan) və EBRD ECEPP (app/scrapers/intl.py)"),
    ("jobsearch", "jobsearch.az — hədəf vəzifələr", "jobs", "https://jobsearch.az", "15 8,11,14,17,20 * * *", True, True,
     "Facility/Camp/Canteen/Procurement/HSE/Site Manager, Chef... (app/scrapers/jobsearch.py)"),
]
CORPORATE_NOTES_OLD = "tourism.gov.az, Rabitəbank, VTB, Azərişıq, Azərenerji (app/scrapers/corporate.py)"
CORPORATE_NOTES_NEW = (
    "tourism.gov.az, Rabitəbank, VTB, Azərişıq, Azərenerji, SOCAR (app/scrapers/corporate.py)"
)


def upgrade() -> None:
    sources_t = sa.table(
        "sources", sa.column("key"), sa.column("name"), sa.column("kind"), sa.column("base_url"),
        sa.column("schedule_cron"), sa.column("respect_robots", sa.Boolean), sa.column("is_active", sa.Boolean),
        sa.column("notes"),
    )
    op.bulk_insert(sources_t, [
        dict(zip(("key", "name", "kind", "base_url", "schedule_cron", "respect_robots", "is_active", "notes"), row))
        for row in NEW_SOURCES
    ])
    op.execute(
        sources_t.update().where(sources_t.c.key == "corporate").values(notes=CORPORATE_NOTES_NEW)
    )


def downgrade() -> None:
    sources_t = sa.table("sources", sa.column("key"), sa.column("notes"))
    op.execute(sources_t.update().where(sources_t.c.key == "corporate").values(notes=CORPORATE_NOTES_OLD))
    keys = [row[0] for row in NEW_SOURCES]
    # Bu mənbələrdən lead gəlibsə FK pozulmasın deyə yalnız istifadə olunmayanlar silinir.
    op.execute(sa.text(
        "DELETE FROM sources WHERE key IN (:a, :b, :c) AND key NOT IN (SELECT source FROM leads)"
    ).bindparams(a=keys[0], b=keys[1], c=keys[2]))
