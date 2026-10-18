from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import structlog
from celery.schedules import crontab
from celery.signals import worker_ready
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import sessionmaker

from app import triggers
from app.celery_app import celery_app
from app.locks import source_lock
from app.db.engine import engine
from app.pipeline import run_pipeline
from app.sources import LOCAL_ONLY, SOURCES, az_proxy

SessionLocal = sessionmaker(bind=engine)
DB_ATTEMPTS = 2
DB_RETRY_DELAY_S = 30
log = structlog.get_logger()

# Sənəd §7 Scheduling:
# - Xəbər/elan saytları: hər 1-2 saatdan bir
# - Etender.gov.az, Emlak.gov.az, LinkedIn: gündə 1 dəfə
#   (LinkedIn Apify actor-u post başına ödənişlidir — tez-tez çağırmaq lazım deyil)

SOURCE_TASKS = {spec.key: spec.scraper for spec in SOURCES}  # reyestrdən (app/sources.py)

# Modal-ı bloklayan mənbələr (Mac worker işlədir) — reyestrdə `local_only=True` (app/sources.py).


def runs_here(source_key: str) -> bool:
    """NEO_SCRAPE_ROLE: "modal" — LOCAL_ONLY-dən başqa hamısı (AZ_PROXY_URL varsa hamısı, LOCAL_ONLY
    proxy ilə); "local" — yalnız LOCAL_ONLY; boş (lokal inkişaf) — hamısı."""
    role = os.environ.get("NEO_SCRAPE_ROLE", "")
    if role == "modal":
        return source_key not in LOCAL_ONLY or az_proxy() is not None
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
        for attempt in range(1, DB_ATTEMPTS + 1):
            session = SessionLocal()
            try:
                return run_pipeline(scraper_cls, session, source_key=source_key, trigger=trigger).pending
            except DBAPIError as exc:
                # Neon bağlantısı düşdü (şəbəkə, Mac yuxusu) — bir dəfə təzə bağlantı ilə
                # təkrar. Qırılmış run-ın yazdığı lead-lər növbəti run-da skorlanır.
                connection_lost = exc.connection_invalidated or isinstance(exc, OperationalError)
                if not connection_lost or attempt == DB_ATTEMPTS:
                    raise
                log.warning("scrape_db_retry", source=source_key, attempt=attempt, error=str(exc)[:200])
                engine.dispose()
                time.sleep(DB_RETRY_DELAY_S)
            finally:
                session.close()


celery_app.conf.beat_schedule = {
    f"scrape-{spec.key}": {"task": "scrape_source", "schedule": crontab(minute=spec.minute, hour=spec.hour),
                           "args": (spec.key,)}
    for spec in SOURCES
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
