from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import structlog
from dotenv import load_dotenv

load_dotenv()
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from app import logbuffer
from app.auth import security
from app.auth.routes import router as auth_router
from app.auth.security import REQUIRE, ROLE_NAMES, CsrfFailed, LoginRequired, PermissionDenied, verify_csrf
from app.db.models import User
from app.ai_filter import SCORE_THRESHOLD
from app.db.engine import engine
from app.db.backfill import backfill
from app.db.migrate import upgrade_to_head
from app.db.repository import LeadRepository
from app.pipeline import PipelineResult, reject_lead, restore_lead, run_pipeline, send_lead_to_crm
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

logbuffer.setup()
log = structlog.get_logger()

SessionLocal = sessionmaker(bind=engine)

SOURCES = {
    "marja": ("Marja.az", MarjaScraper),
    # azerweb qəsdən yoxdur — robots.txt `Disallow: /api/`, məzmun isə yalnız API-dən gəlir (bax app/scrapers/azerweb.py)
    "1news": ("1news.az", News1Scraper),
    "oxu": ("Oxu.az", OxuScraper),
    "fed": ("Fed.az", FedScraper),
    "fed_tenders": ("Fed.az Tenderlər", FedTenderScraper),
    "report": ("Report.az Sənaye", ReportSenayeScraper),
    "etender": ("Etender.gov.az", EtenderScraper),
    "emlak": ("Emlak.gov.az", EmlakScraper),
    "corporate": ("Şirkət/agentlik tenderləri", CorporateTenderScraper),
    "worldbank": ("Dünya Bankı", WorldBankScraper),
    "bp_edp": ("bp EDP", BpEdpScraper),
    "boss": ("boss.az — işçilərinə nahar verən şirkətlər", BossAzScraper),
    "jobsearch": ("jobsearch.az — hədəf vəzifələr", JobSearchScraper),
    "media": ("Media, assosiasiya və sərgilər", MediaScraper),
    "intl_tender": ("UNGM + EBRD tenderləri", IntlTenderScraper),
    "linkedin": ("LinkedIn (Apify)", LinkedInScraper),
}

_last_run: dict[str, str] = {}

# "Hamısını scrape et": hər mənbə ayrı thread-də, öz DB sessiyası ilə.
# _running eyni mənbənin iki dəfə eyni vaxtda işləməsinin qarşısını alır.
_running: set[str] = set()
_running_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=len(SOURCES), thread_name_prefix="scrape")


def _claim(keys: list[str]) -> list[str]:
    """İşləməyən mənbələri 'işləyir' kimi qeyd et, onları qaytar."""
    with _running_lock:
        free = [k for k in keys if k not in _running]
        _running.update(free)
    return free


def _run_source(source_key: str, trigger: str = "manual") -> PipelineResult | None:
    label, scraper_cls = SOURCES[source_key]
    logbuffer.bind_source(source_key)
    session = SessionLocal()
    try:
        result = run_pipeline(scraper_cls, session, source_key=source_key, trigger=trigger)
        _last_run[source_key] = f"{datetime.now().strftime('%H:%M:%S')} — {result.summary()}"
        return result
    except Exception:
        log.exception("scrape_failed", source=source_key)
        _last_run[source_key] = f"{datetime.now().strftime('%H:%M:%S')} — XƏTA, loglara bax"
        return None
    finally:
        session.close()
        logbuffer.unbind_source()
        with _running_lock:
            _running.discard(source_key)

app = FastAPI(title="Lead Scraping Admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.state.templates = templates
# Giriş sessiyası imzalı cookie-də; SameSite=Lax + formlarda CSRF token.
app.add_middleware(
    SessionMiddleware, secret_key=security.session_secret(), session_cookie="neo_session",
    max_age=security.SESSION_MAX_AGE, same_site="lax",
)
app.include_router(auth_router)
templates.env.globals.update(
    csrf_field=security.csrf_field, has_role=security.has_role, role_names=ROLE_NAMES,
)


@app.exception_handler(LoginRequired)
def _login_required(request: Request, exc: LoginRequired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "login_required"}, status_code=401)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if request.method != "GET":
        target = "/"
    return RedirectResponse(url=f"/login?next={quote(target)}", status_code=303)


@app.exception_handler(PermissionDenied)
def _permission_denied(request: Request, exc: PermissionDenied):
    user = request.state.user
    return templates.TemplateResponse("forbidden.html", {
        "request": request, "required_name": ROLE_NAMES[exc.required],
        "current_name": ROLE_NAMES[user.role_code] if user else "—",
    }, status_code=403)


@app.exception_handler(CsrfFailed)
def _csrf_failed(request: Request, exc: CsrfFailed):
    return RedirectResponse(url=f"/?err={quote('Forma köhnəlib — səhifəni yenilə və yenidən cəhd et')}", status_code=303)


def _baku_time(value: datetime | None) -> str:
    """DB-də naive UTC saxlanılır — paneldə Bakı vaxtı (+04) göstərilir."""
    return (value + timedelta(hours=4)).strftime("%d.%m.%Y %H:%M") if value else "—"


templates.env.filters["baku_time"] = _baku_time
templates.env.globals["utcnow"] = datetime.utcnow  # son tarixin keçib-keçmədiyi üçün


@app.on_event("startup")
def on_startup() -> None:
    # Baza Alembic miqrasiyaları ilə ən son sxemə gətirilir (köhnə baza da
    # itkisiz yenilənir) — bax app/db/migrate.py. Sonra köhnə lead-lər yeni
    # sahələrlə (son tarix, tender) doldurulur — idempotent, bax app/db/backfill.py.
    upgrade_to_head(engine)
    session = SessionLocal()
    try:
        backfill(session)
    finally:
        session.close()


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _last_runs_from_db() -> dict[str, str]:
    """Hər mənbənin bazadakı son işə salınması, dashboard kartı üçün mətn."""
    session = SessionLocal()
    try:
        runs = LeadRepository(session).latest_runs()
    finally:
        session.close()
    out = {}
    for key, run in runs.items():
        when = _baku_time(run.started_at)
        if run.status == "failed":
            out[key] = f"{when} — XƏTA, loglara bax"
        elif run.status == "running":
            out[key] = f"{when} — başladı, bitmədi"
        else:
            out[key] = f"{when} — {run.leads_new} yeni lead, {run.leads_pending} validasiyaya"
    return out


@app.get("/")
def dashboard(request: Request, user: User = Depends(REQUIRE["viewer"])):
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
            # Bu prosesdə işə salınanlar canlı; qalanları bazadan (server yenidən başlayanda itmir).
            "last_run": {**_last_runs_from_db(), **_last_run},
            "running": set(_running),
            "stats": stats,
        },
    )


@app.post("/scrape/{source_key}", dependencies=[Depends(verify_csrf)])
def scrape_now(source_key: str, user: User = Depends(REQUIRE["manager"])):
    """'Scrape indi' düyməsi — validation göstərilir: naməlum mənbə istəyi rədd edilir."""
    if source_key not in SOURCES:
        return RedirectResponse(
            url=f"/?err=Naməlum mənbə: {source_key}", status_code=303
        )

    label = SOURCES[source_key][0]
    if not _claim([source_key]):
        return RedirectResponse(url=f"/?err={label} artıq işləyir — Loglar səhifəsində izlə", status_code=303)

    result = _run_source(source_key)
    if result is None:
        return RedirectResponse(
            url=f"/?err=Scrape zamanı xəta baş verdi ({label}) — Loglar səhifəsinə bax",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/?msg={label}: {result.summary()}",
        status_code=303,
    )


@app.post("/scrape-all", dependencies=[Depends(verify_csrf)])
def scrape_all(user: User = Depends(REQUIRE["manager"])):
    """Bütün mənbələri paralel, fonda işə salır — cavab dərhal qayıdır, gedişat
    dashboard-da və /logs-da canlı görünür."""
    started = _claim(list(SOURCES))
    if not started:
        return RedirectResponse(url="/?err=Bütün mənbələr artıq işləyir — Loglar səhifəsində izlə", status_code=303)

    log.info("scrape_all_started", sources=",".join(started))
    for key in started:
        _last_run[key] = "işləyir…"
        _executor.submit(_run_source, key, "scrape_all")

    skipped = len(SOURCES) - len(started)
    note = f" ({skipped} mənbə artıq işləyirdi)" if skipped else ""
    return RedirectResponse(url=f"/logs?msg={len(started)} mənbə paralel işə salındı{note}", status_code=303)


@app.get("/logs")
def logs_page(request: Request, user: User = Depends(REQUIRE["viewer"])):
    return templates.TemplateResponse(
        "logs.html",
        {"request": request, "active": "logs", "sources": {k: v[0] for k, v in SOURCES.items()}},
    )


@app.get("/api/logs")
def logs_api(after: int = 0, source: str | None = None, level: str | None = None, user: User = Depends(REQUIRE["viewer"])):
    with _running_lock:
        running = sorted(_running)
    return JSONResponse({
        "entries": logbuffer.entries(after=after, source=source or None, level=level or None),
        "running": running,
    })


@app.get("/leads/pending_review")
def leads_pending(request: Request, user: User = Depends(REQUIRE["viewer"])):
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


@app.get("/leads/ai_rejected")
def leads_ai_rejected(request: Request, user: User = Depends(REQUIRE["viewer"])):
    session = SessionLocal()
    try:
        repo = LeadRepository(session)
        leads = repo.list_ai_rejected(SCORE_THRESHOLD)
    finally:
        session.close()

    return templates.TemplateResponse(
        "leads.html",
        {
            "request": request,
            "active": "ai_rejected",
            "heading": "AI rədd etdi",
            "subtitle": (
                f"AI skoru {SCORE_THRESHOLD} eşiyindən aşağı olan lead-lər. "
                "Səhv rədd görsən, Validasiyaya qaytar."
            ),
            "leads": leads,
            "show_actions": False,
            "show_restore": True,
        },
    )


@app.get("/leads")
def leads_all(request: Request, user: User = Depends(REQUIRE["viewer"])):
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


@app.post("/leads/{dedupe_key}/approve", dependencies=[Depends(verify_csrf)])
def approve_lead(dedupe_key: str, user: User = Depends(REQUIRE["sales"])):
    # Validation: mövcud olmayan və ya artıq emal edilmiş lead üçün aydın xəta.
    if not dedupe_key or len(dedupe_key) != 64:
        return RedirectResponse(url="/leads/pending_review?err=Yanlış lead ID", status_code=303)

    session = SessionLocal()
    try:
        result = send_lead_to_crm(dedupe_key, session, reviewer_id=user.id)
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


@app.post("/leads/{dedupe_key}/restore", dependencies=[Depends(verify_csrf)])
def restore_lead_route(dedupe_key: str, user: User = Depends(REQUIRE["sales"])):
    if not dedupe_key or len(dedupe_key) != 64:
        return RedirectResponse(url="/leads/ai_rejected?err=Yanlış lead ID", status_code=303)

    session = SessionLocal()
    try:
        ok = restore_lead(dedupe_key, session, reviewer_id=user.id)
    finally:
        session.close()

    if not ok:
        return RedirectResponse(
            url="/leads/ai_rejected?err=Lead tapılmadı və ya artıq rədd statusunda deyil",
            status_code=303,
        )
    return RedirectResponse(url="/leads/ai_rejected?msg=Lead Validasiyaya qaytarıldı", status_code=303)


@app.post("/leads/{dedupe_key}/reject", dependencies=[Depends(verify_csrf)])
def reject_lead_route(dedupe_key: str, user: User = Depends(REQUIRE["sales"])):
    if not dedupe_key or len(dedupe_key) != 64:
        return RedirectResponse(url="/leads/pending_review?err=Yanlış lead ID", status_code=303)

    session = SessionLocal()
    try:
        ok = reject_lead(dedupe_key, session, reviewer_id=user.id)
    finally:
        session.close()

    if not ok:
        return RedirectResponse(
            url="/leads/pending_review?err=Lead tapılmadı və ya artıq emal olunub (yalnız pending_review rədd edilə bilər)",
            status_code=303,
        )
    return RedirectResponse(url="/leads/pending_review?msg=Lead rədd edildi", status_code=303)
