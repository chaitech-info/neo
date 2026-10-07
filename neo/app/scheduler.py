from __future__ import annotations

from celery.schedules import crontab
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.db.engine import engine
from app.pipeline import run_pipeline
from app.scrapers import (
    BossAzScraper,
    BpEdpScraper,
    CorporateTenderScraper,
    EmlakScraper,
    EtenderScraper,
    FedScraper,
    FedTenderScraper,
    IntlTenderScraper,
    JobSearchScraper,
    LinkedInScraper,
    MarjaScraper,
    MediaScraper,
    News1Scraper,
    OxuScraper,
    ReportSenayeScraper,
    WorldBankScraper,
)

SessionLocal = sessionmaker(bind=engine)

# Sənəd §7 Scheduling:
# - Xəbər/elan saytları: hər 1-2 saatdan bir
# - Etender.gov.az, Emlak.gov.az, LinkedIn: gündə 1 dəfə
#   (LinkedIn Apify actor-u post başına ödənişlidir — tez-tez çağırmaq lazım deyil)

SOURCE_TASKS = {
    "marja": MarjaScraper,
    # azerweb qəsdən yoxdur — robots.txt `Disallow: /api/`, məzmun isə yalnız API-dən gəlir (bax app/scrapers/azerweb.py)
    "1news": News1Scraper,
    "oxu": OxuScraper,
    "fed": FedScraper,
    "fed_tenders": FedTenderScraper,
    "report": ReportSenayeScraper,
    "etender": EtenderScraper,
    "emlak": EmlakScraper,
    "corporate": CorporateTenderScraper,
    "worldbank": WorldBankScraper,
    "bp_edp": BpEdpScraper,
    "boss": BossAzScraper,
    "jobsearch": JobSearchScraper,
    "media": MediaScraper,
    "intl_tender": IntlTenderScraper,
    "linkedin": LinkedInScraper,
}


@celery_app.task(name="scrape_source")
def scrape_source(source_key: str) -> int:
    scraper_cls = SOURCE_TASKS[source_key]
    session = SessionLocal()
    try:
        return run_pipeline(scraper_cls, session, source_key=source_key, trigger="schedule").pending
    finally:
        session.close()


celery_app.conf.beat_schedule = {
    "scrape-marja-every-90min": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour="*/1,*/2"),
        "args": ("marja",),
    },
    "scrape-1news-hourly": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour="*"),
        "args": ("1news",),
    },
    "scrape-oxu-hourly": {
        "task": "scrape_source",
        "schedule": crontab(minute=20, hour="*"),
        "args": ("oxu",),
    },
    "scrape-fed-hourly": {
        "task": "scrape_source",
        "schedule": crontab(minute=40, hour="*"),
        "args": ("fed",),
    },
    "scrape-fed-tenders-every-2h": {
        "task": "scrape_source",
        "schedule": crontab(minute=50, hour="*/2"),
        "args": ("fed_tenders",),
    },
    "scrape-report-every-2h": {
        "task": "scrape_source",
        "schedule": crontab(minute=25, hour="*/2"),
        "args": ("report",),
    },
    "scrape-etender-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour=6),
        "args": ("etender",),
    },
    "scrape-corporate-twice-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=10, hour="8,16"),
        "args": ("corporate",),
    },
    "scrape-worldbank-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour=7),
        "args": ("worldbank",),
    },
    "scrape-bp-edp-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=15, hour=7),
        "args": ("bp_edp",),
    },
    "scrape-emlak-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=30, hour=6),
        "args": ("emlak",),
    },
    "scrape-linkedin-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour=7),
        "args": ("linkedin",),
    },
    # ~28 GraphQL səhifəsi, ~1.5 dəq. Elanlar 30 gün aktiv qalır — gündə 1 dəfə kifayətdir.
    "scrape-boss-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=45, hour=7),
        "args": ("boss",),
    },
    # jobsearch.az yalnız ən yeni ~30 elanı göstərir (səhifələmə yoxdur) — gündə 5 dəfə.
    "scrape-jobsearch-5x-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=15, hour="8,11,14,17,20"),
        "args": ("jobsearch",),
    },
    # 16 sayt × (siyahı + seçilmiş məqalələr) — saatlıq lazım deyil, gündə 2 dəfə kifayətdir.
    "scrape-media-twice-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=35, hour="9,15"),
        "args": ("media",),
    },
    "scrape-intl-tender-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=30, hour=7),
        "args": ("intl_tender",),
    },
}
