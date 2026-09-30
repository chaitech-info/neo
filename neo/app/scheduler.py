from __future__ import annotations

import os
from datetime import datetime, timedelta

import structlog
from celery.schedules import crontab
from celery.signals import worker_ready
from sqlalchemy.orm import sessionmaker

from app import triggers
from app.celery_app import celery_app
from app.locks import source_lock
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
    LinkedInScraper,
    MarjaScraper,
    News1Scraper,
    OxuScraper,
    ReportSenayeScraper,
    WorldBankScraper,
)

SessionLocal = sessionmaker(bind=engine)
log = structlog.get_logger()

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
    "linkedin": LinkedInScraper,
}

# Modal-ın (ABŞ/AB datacenter) IP-lərini bloklayan mənbələr — 403 və ya timeout
# (2026-09-30 yoxlanıb; report ABŞ IP-dən 403, AB-dən bəzən 200 — qeyri-sabit). Bunlar Azərbaycan IP-si olan maşında (lokal Mac worker) işləyir.
LOCAL_ONLY = frozenset({"etender", "fed", "fed_tenders", "oxu", "report"})


def runs_here(source_key: str) -> bool:
    """NEO_SCRAPE_ROLE: "modal" — LOCAL_ONLY-dən başqa hamısı; "local" — yalnız
    LOCAL_ONLY; boş (lokal inkişaf) — hamısı."""
    role = os.environ.get("NEO_SCRAPE_ROLE", "")
    if role == "modal":
        return source_key not in LOCAL_ONLY
    if role == "local":
        return source_key in LOCAL_ONLY
    return True


@celery_app.task(name="scrape_source")
def scrape_source(source_key: str, trigger: str = "schedule") -> int | None:
    """Planlı scrape (və ya Vercel panelindən gələn sorğu — trigger manual/scrape_all).
    Mənbə artıq işləyirsə — atlanır, None qaytarır."""
    scraper_cls = SOURCE_TASKS[source_key]
    if not runs_here(source_key):
        # Mac worker-in beat-i bütün cədvəli göndərir — Modal-ın mənbələrini burada atlayırıq.
        log.debug("scrape_skipped_other_host", source=source_key, role=os.environ.get("NEO_SCRAPE_ROLE"))
        return None
    with source_lock(source_key) as acquired:
        if not acquired:
            log.info("scheduled_scrape_skipped", source=source_key, reason="artıq işləyir")
            return None
        session = SessionLocal()
        try:
            return run_pipeline(scraper_cls, session, source_key=source_key, trigger=trigger).pending
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
}


def due_sources(now: datetime, window_min: int) -> list[str]:
    """Modal cron-u (modal_app.py::tick) üçün: `now`-dan əvvəlki `window_min`
    dəqiqədə (now daxil) cədvəli düşən mənbələr. `now` — Bakı vaxtı. Eyni
    beat_schedule həm Celery-də, həm Modal-da işləyir."""
    minutes = [now - timedelta(minutes=i) for i in range(window_min)]
    due = []
    for entry in celery_app.conf.beat_schedule.values():
        cron, key = entry["schedule"], entry["args"][0]
        if key in due or not runs_here(key):
            continue
        if any(t.minute in cron.minute and t.hour in cron.hour
               and (t.isoweekday() % 7) in cron.day_of_week for t in minutes):
            due.append(key)
    return due


def _dispatch_local(source_key: str, trigger: str) -> None:
    if source_key not in LOCAL_ONLY or trigger not in ("manual", "scrape_all"):
        log.warning("local_request_ignored", source=source_key, trigger=trigger)
        return
    scrape_source.apply_async(args=[source_key, trigger])


@worker_ready.connect
def _start_local_bridge(**_) -> None:
    # Mac worker: Vercel panelindən LOCAL_ONLY mənbələr üçün "Scrape indi" (Modal Queue).
    if os.environ.get("NEO_SCRAPE_ROLE") == "local":
        triggers.start_local_bridge(_dispatch_local)
