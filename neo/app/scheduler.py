from __future__ import annotations

from celery.schedules import crontab
from sqlalchemy.orm import sessionmaker

from app.celery_app import celery_app
from app.db.engine import engine
from app.pipeline import run_pipeline
from app.scrapers import (
    AzerwebScraper,
    EmlakScraper,
    EtenderScraper,
    FedScraper,
    LinkedInScraper,
    MarjaScraper,
    News1Scraper,
    OxuScraper,
)

SessionLocal = sessionmaker(bind=engine)

# Sənəd §7 Scheduling:
# - Xəbər/elan saytları: hər 1-2 saatdan bir
# - Etender.gov.az, Emlak.gov.az, LinkedIn: gündə 1 dəfə
#   (LinkedIn Apify actor-u post başına ödənişlidir — tez-tez çağırmaq lazım deyil)

SOURCE_TASKS = {
    "marja": MarjaScraper,
    "azerweb": AzerwebScraper,
    "1news": News1Scraper,
    "oxu": OxuScraper,
    "fed": FedScraper,
    "etender": EtenderScraper,
    "emlak": EmlakScraper,
    "linkedin": LinkedInScraper,
}


@celery_app.task(name="scrape_source")
def scrape_source(source_key: str) -> int:
    scraper_cls = SOURCE_TASKS[source_key]
    session = SessionLocal()
    try:
        return run_pipeline(scraper_cls, session)
    finally:
        session.close()


celery_app.conf.beat_schedule = {
    "scrape-marja-every-90min": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour="*/1,*/2"),
        "args": ("marja",),
    },
    "scrape-azerweb-every-90min": {
        "task": "scrape_source",
        "schedule": crontab(minute=15, hour="*/2"),
        "args": ("azerweb",),
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
    "scrape-etender-daily": {
        "task": "scrape_source",
        "schedule": crontab(minute=0, hour=6),
        "args": ("etender",),
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
}
