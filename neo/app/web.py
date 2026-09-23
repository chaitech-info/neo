from __future__ import annotations

from pathlib import Path

import structlog
from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from app.db.engine import engine
from app.db.models import Base
from app.db.repository import LeadRepository
from app.pipeline import reject_lead, run_pipeline, send_lead_to_crm
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

log = structlog.get_logger()

SessionLocal = sessionmaker(bind=engine)

SOURCES = {
    "marja": ("Marja.az", MarjaScraper),
    "azerweb": ("Azerweb.az", AzerwebScraper),
    "1news": ("1news.az", News1Scraper),
    "oxu": ("Oxu.az", OxuScraper),
    "fed": ("Fed.az", FedScraper),
    "etender": ("Etender.gov.az", EtenderScraper),
    "emlak": ("Emlak.gov.az", EmlakScraper),
    "linkedin": ("LinkedIn (Apify)", LinkedInScraper),
}

_last_run: dict[str, str] = {}

app = FastAPI(title="Lead Scraping Admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def on_startup() -> None:
    # Development/test rahatlığı üçün cədvəllər avtomatik yaradılır.
    # Production-da bunun əvəzinə `python -m scripts.init_db` + miqrasiya alətindən istifadə et.
    Base.metadata.create_all(engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@app.get("/")
def dashboard(request: Request):
    session = SessionLocal()
    try:
        repo = LeadRepository(session)
        stats: dict[str, int] = {}
        for status in ["new", "pending_review", "sent_to_crm", "rejected", "duplicate"]:
            stats[status] = len(repo.list_by_status(status))
    finally:
        session.close()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "active": "dashboard",
            "sources": {k: v[0] for k, v in SOURCES.items()},
            "last_run": _last_run,
            "stats": stats,
        },
    )


@app.post("/scrape/{source_key}")
def scrape_now(source_key: str):
    """'Scrape indi' düyməsi — validation göstərilir: naməlum mənbə istəyi rədd edilir."""
    if source_key not in SOURCES:
        return RedirectResponse(
            url=f"/?err=Naməlum mənbə: {source_key}", status_code=303
        )

    label, scraper_cls = SOURCES[source_key]
    session = SessionLocal()
    try:
        pending_count = run_pipeline(scraper_cls, session)
    except Exception:
        log.exception("manual_scrape_failed", source=source_key)
        return RedirectResponse(
            url=f"/?err=Scrape zamanı xəta baş verdi ({label}) — logları yoxla",
            status_code=303,
        )
    finally:
        session.close()

    from datetime import datetime

    _last_run[source_key] = f"{datetime.now().strftime('%H:%M:%S')} — {pending_count} yeni lead validasiya gözləyir"

    return RedirectResponse(
        url=f"/?msg={label}: {pending_count} yeni lead validasiya üçün əlavə olundu",
        status_code=303,
    )


@app.get("/leads/pending_review")
def leads_pending(request: Request):
    session = SessionLocal()
    try:
        repo = LeadRepository(session)
        leads = repo.list_by_status("pending_review")
    finally:
        session.close()

    return templates.TemplateResponse(
        "leads.html",
        {
            "request": request,
            "active": "validation",
            "heading": "Validasiya",
            "subtitle": "AI tərəfindən uyğun sayılan lead-lər — CRM-ə göndərməzdən əvvəl təsdiqlə və ya rədd et.",
            "leads": leads,
            "show_actions": True,
        },
    )


@app.get("/leads")
def leads_all(request: Request):
    session = SessionLocal()
    try:
        repo = LeadRepository(session)
        leads = repo.list_all()
    finally:
        session.close()

    return templates.TemplateResponse(
        "leads.html",
        {
            "request": request,
            "active": "all",
            "heading": "Bütün lead-lər",
            "subtitle": "Son 200 scrape olunmuş lead (bütün statuslar).",
            "leads": leads,
            "show_actions": False,
        },
    )


@app.post("/leads/{dedupe_key}/approve")
def approve_lead(dedupe_key: str):
    # Validation: mövcud olmayan və ya artıq emal edilmiş lead üçün aydın xəta.
    if not dedupe_key or len(dedupe_key) != 64:
        return RedirectResponse(url="/leads/pending_review?err=Yanlış lead ID", status_code=303)

    session = SessionLocal()
    try:
        result = send_lead_to_crm(dedupe_key, session)
    finally:
        session.close()

    if result == "not_found":
        return RedirectResponse(
            url="/leads/pending_review?err=Lead tapılmadı və ya artıq emal olunub (yalnız pending_review təsdiqlənə bilər)",
            status_code=303,
        )
    if result == "crm_failed":
        return RedirectResponse(
            url="/leads/pending_review?err=CRM-ə göndərilmədi — CRM_BASE_URL hələ real endpoint-ə işarə etmir (bax .env, sənəd §9)",
            status_code=303,
        )
    return RedirectResponse(url="/leads/pending_review?msg=Lead CRM-ə göndərildi", status_code=303)


@app.post("/leads/{dedupe_key}/reject")
def reject_lead_route(dedupe_key: str):
    if not dedupe_key or len(dedupe_key) != 64:
        return RedirectResponse(url="/leads/pending_review?err=Yanlış lead ID", status_code=303)

    session = SessionLocal()
    try:
        ok = reject_lead(dedupe_key, session)
    finally:
        session.close()

    if not ok:
        return RedirectResponse(url="/leads/pending_review?err=Lead tapılmadı", status_code=303)
    return RedirectResponse(url="/leads/pending_review?msg=Lead rədd edildi", status_code=303)
