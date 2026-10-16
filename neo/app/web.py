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
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from app import logbuffer
from app.auth import security
from app.auth.routes import router as auth_router
from app.auth.security import REQUIRE, ROLE_NAMES, CsrfFailed, LoginRequired, PermissionDenied, verify_csrf
from app.db.models import User
from app.ai_filter import SCORE_THRESHOLD
from app import triggers
from app.health import source_health
from app.db.engine import SERVERLESS, engine
from app.db.backfill import backfill
from app.db.migrate import upgrade_to_head
from app.db.repository import LeadRepository
from app import crm
from app.crm_routes import router as crm_router
from app.keyword_routes import router as keyword_router
from app.offer_routes import router as offer_router
from app.locks import source_lock
from app.pipeline import PipelineResult, reject_lead, restore_lead, run_pipeline
from app.sources import SOURCES as SOURCE_SPECS

logbuffer.setup()
log = structlog.get_logger()

SessionLocal = sessionmaker(bind=engine)

SOURCES = {spec.key: (spec.label, spec.scraper) for spec in SOURCE_SPECS}  # reyestr: app/sources.py

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
    try:
        with source_lock(source_key) as acquired:
            if not acquired:
                # Avtomatik (Celery) scrape həmin mənbəni indi işlədir — ikinci run lazım deyil.
                _last_run[source_key] = f"{datetime.now().strftime('%H:%M:%S')} — atlandı: avtomatik scrape artıq işləyir"
                log.info("manual_scrape_skipped", source=source_key, reason="avtomatik scrape işləyir")
                return PipelineResult()
            session = SessionLocal()
            try:
                result = run_pipeline(scraper_cls, session, source_key=source_key, trigger=trigger)
            finally:
                session.close()
        _last_run[source_key] = f"{datetime.now().strftime('%H:%M:%S')} — {result.summary()}"
        return result
    except Exception:
        log.exception("scrape_failed", source=source_key)
        _last_run[source_key] = f"{datetime.now().strftime('%H:%M:%S')} — XƏTA, loglara bax"
        return None
    finally:
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
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(crm_router)
app.include_router(keyword_router)
app.include_router(offer_router)
templates.env.globals.update(
    csrf_field=security.csrf_field, has_role=security.has_role, role_names=ROLE_NAMES,
)

# Mənbənin növü — dashboard və validasiya qəbzlərində rəng/etiket.
SOURCE_KIND = {spec.key: spec.panel_kind for spec in SOURCE_SPECS}
KIND_NAMES = {"tender": "Tender", "employer": "İşəgötürən", "news": "Xəbər"}


def lead_kind(source: str) -> str:
    return SOURCE_KIND.get(source, "news")


def nav_counts(user: User | None) -> dict[str, int]:
    """Sol menyudakı sayğaclar: validasiyada gözləyən, mənim açıq/gecikən tapşırıqlarım."""
    from sqlalchemy import func, select

    from app.db.models import LeadRecord, Task

    if user is None:
        return {}
    try:
        with SessionLocal() as db:
            pending = db.scalar(select(func.count()).select_from(LeadRecord).where(LeadRecord.status == "pending_review"))
            mine = select(func.count()).select_from(Task).where(
                Task.assignee_id == user.id, Task.status.in_(("open", "in_progress")))
            open_tasks = db.scalar(mine)
            late = db.scalar(mine.where(Task.due_at < datetime.utcnow()))
    except Exception:
        log.warning("nav_counts_failed", exc_info=True)
        return {}
    return {"pending": pending or 0, "tasks": open_tasks or 0, "late": late or 0}


templates.env.globals.update(nav_counts=nav_counts, lead_kind=lead_kind, kind_names=KIND_NAMES)


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
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Bu əməliyyat üçün icazən yoxdur"}, status_code=403)
    return templates.TemplateResponse(request, "forbidden.html", {
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
    # Vercel-də hər soyuq başlanğıcda miqrasiya etmirik (yavaş, paralel instansiyalar
    # üst-üstə düşər) — orada baza bir dəfə `python -m scripts.init_db` ilə hazırlanır.
    if SERVERLESS:
        return
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


def _schedule_text(cron) -> str:
    """Celery crontab → "hər saat, :20" / "2 saatdan bir, :25" / "hər gün 06:00"."""
    minute, hour = str(cron._orig_minute), str(cron._orig_hour)
    mm = f":{int(minute):02d}" if minute.isdigit() else ""
    hours = hour.split(",")
    if hour == "*" or "*/1" in hours:
        return f"hər saat{', ' + mm if mm else ''}"
    if hour.startswith("*/") and len(hours) == 1:
        return f"{hour[2:]} saatdan bir{', ' + mm if mm else ''}"
    if all(h.isdigit() for h in hours):
        times = ", ".join(f"{int(h):02d}{mm or ':00'}" for h in hours)
        return f"hər gün {times}" if len(hours) == 1 else f"gündə {len(hours)} dəfə: {times}"
    return str(cron)


def _short_time(value: datetime | None) -> str | None:
    """Bu gündürsə yalnız saat ("21:00"), əks halda "29.09 21:00" (Bakı vaxtı)."""
    if value is None:
        return None
    local, today = value + timedelta(hours=4), (datetime.utcnow() + timedelta(hours=4)).date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%d.%m %H:%M")


def _schedules() -> dict[str, str]:
    try:
        from app.scheduler import celery_app

        return {v["args"][0]: _schedule_text(v["schedule"]) for v in celery_app.conf.beat_schedule.values()}
    except Exception:
        log.warning("schedule_text_failed", exc_info=True)
        return {}


def _crons() -> dict[str, object]:
    try:
        from app.scheduler import celery_app

        return {v["args"][0]: v["schedule"] for v in celery_app.conf.beat_schedule.values()}
    except Exception:
        log.warning("schedule_load_failed", exc_info=True)
        return {}


@app.get("/")
def dashboard(request: Request, user: User = Depends(REQUIRE["viewer"])):
    from sqlalchemy import func, select

    from app.db.models import LeadRecord, Opportunity, ScrapeRun

    session = SessionLocal()
    try:
        repo = LeadRepository(session)
        stats = {status: len(repo.list_by_status(status))
                 for status in ["new", "pending_review", "converted", "rejected", "duplicate"]}
        tier_counts = dict(session.execute(
            select(LeadRecord.tier, func.count()).where(LeadRecord.status == "pending_review",
                                                        LeadRecord.tier.is_not(None)).group_by(LeadRecord.tier)).all())
        open_opps = session.scalar(select(func.count()).select_from(Opportunity)
                                   .where(Opportunity.stage.notin_(("won", "lost"))))
        last_auto = session.scalar(select(func.max(ScrapeRun.started_at)).where(ScrapeRun.trigger == "schedule"))
        health = source_health(session, _crons())
    finally:
        session.close()

    last_run = {**_last_runs_from_db(), **_last_run}  # bu prosesdə işə salınanlar canlı, qalanı bazadan
    schedules = _schedules()
    sources = [
        {"key": k, "label": label, "kind": lead_kind(k), "schedule": schedules.get(k, "—"),
         "last": last_run.get(k), "health": health.get(k)}
        for k, (label, _) in SOURCES.items()
    ]
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "request": request,
            "active": "dashboard",
            "sources": sources,
            "running": set(_running),
            "stats": stats,
            "open_opps": open_opps or 0,
            "tier_counts": tier_counts,
            "last_auto": _short_time(last_auto),
        },
    )


def _enqueue(keys: list[str], trigger: str) -> str | None:
    """Vercel-də scrape funksiyanın içində işləyə bilməz (cavabdan sonra fon işi
    öldürülür, uzun mənbələr vaxt limitini keçir) — sorğu Modal-a gedir, scrape
    orada fonda işləyir (app/triggers.py, modal_app.py). Xəta mətni və ya None."""
    try:
        triggers.request(keys, trigger)
    except Exception:
        log.exception("enqueue_failed", sources=",".join(keys))
        return ("Növbəyə qoyulmadı: panel Modal-a çata bilmir — Vercel-də MODAL_TRIGGER_URL və "
                "TRIGGER_TOKEN yoxlanmalıdır. Planlı scraping Modal-da işləməyə davam edir.")
    return None


@app.post("/scrape/{source_key}", dependencies=[Depends(verify_csrf)])
def scrape_now(source_key: str, user: User = Depends(REQUIRE["manager"])):
    """'Scrape indi' düyməsi — validation göstərilir: naməlum mənbə istəyi rədd edilir."""
    if source_key not in SOURCES:
        return RedirectResponse(
            url=f"/?err=Naməlum mənbə: {source_key}", status_code=303
        )

    label = SOURCES[source_key][0]
    if SERVERLESS:
        err = _enqueue([source_key], "manual")
        if err:
            return RedirectResponse(url=f"/?err={quote(err)}", status_code=303)
        return RedirectResponse(url=f"/?msg={quote(f'{label} işə salındı — nəticə bir neçə dəqiqəyə dashboard-da görünəcək')}", status_code=303)
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
    if SERVERLESS:
        err = _enqueue(list(SOURCES), "scrape_all")
        if err:
            return RedirectResponse(url=f"/?err={quote(err)}", status_code=303)
        return RedirectResponse(url=f"/?msg={quote(f'{len(SOURCES)} mənbə işə salındı — nəticələr bir neçə dəqiqəyə dashboard-da görünəcək')}", status_code=303)
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
        request, "logs.html",
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


TIER_ORDER = {"A": 0, "B": 1, "C": 2}
# Kateqoriya meyarları (app/ai_scoring.py::compute_score) — paneldə izah kimi göstərilir.
TIER_INFO = {
    "A": ("Birbaşa tələbat", "yemək/catering/iaşə tenderi, ərzaq alışı, yeməyi açıq yazan tədbir tenderi"),
    "B": ("Güclü dolayı", "tədbir təşkili tenderi, işçilərinə nahar verən orta/böyük şirkət, yeni zavod/obyekt, sərgi"),
    "C": ("Zəif / erkən", "kiçik işəgötürən, erkən mərhələli layihə, xəbərdə yeməyi qeyd olunan tədbir"),
}


def tier_sort_key(lead) -> tuple:
    """A → B → C → kateqoriyasız (v1/skorsuz); daxilində yüksək skor və yaxın son tarix əvvəl."""
    deadline = lead.deadline_at.timestamp() if lead.deadline_at else float("inf")
    return (TIER_ORDER.get(lead.tier, 3), -(lead.ai_score or 0), deadline)


@app.get("/leads/pending_review")
def leads_pending(request: Request, tier: str = "", user: User = Depends(REQUIRE["viewer"])):
    session = SessionLocal()
    try:
        repo = LeadRepository(session)
        all_leads = sorted(repo.list_by_status("pending_review"), key=tier_sort_key)
    finally:
        session.close()
    tier_counts = {t: sum(1 for lead in all_leads if lead.tier == t) for t in TIER_ORDER}
    tier_counts["none"] = len(all_leads) - sum(tier_counts.values())
    tier = tier if tier in tier_counts else ""
    leads = [lead for lead in all_leads if not tier or (lead.tier or "none") == tier]

    return templates.TemplateResponse(
        request, "leads.html",
        {
            "request": request,
            "active": "validation",
            "heading": "Validasiya",
            "subtitle": "AI-nin satış fürsəti saydığı lead-lər, A → B → C sırası ilə. Uyğundursa fürsət yarat — ilk tapşırıq avtomatik açılır; deyilsə rədd et.",
            "leads": leads,
            "show_actions": True,
            "tier": tier,
            "tier_counts": tier_counts,
            "tier_info": TIER_INFO,
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
        request, "leads.html",
        {
            "request": request,
            "active": "ai_rejected",
            "heading": "AI rədd etdi",
            "subtitle": (
                f"AI skoru {int(SCORE_THRESHOLD * 100)}-dən aşağı olan lead-lər. "
                "Fürsət görsən, Validasiyaya qaytar."
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
        request, "leads.html",
        {
            "request": request,
            "active": "all",
            "heading": "Bütün lead-lər",
            "subtitle": "Son 200 lead, bütün statuslar.",
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
        conversion = crm.convert_lead(session, dedupe_key, user.id)
        opp_id = conversion.opportunity.id
    except crm.CrmError as exc:
        return RedirectResponse(url=f"/leads/pending_review?err={quote(str(exc))}", status_code=303)
    finally:
        session.close()

    msg = quote(f"Fürsət #{opp_id} və ilk tapşırıq yaradıldı")
    return RedirectResponse(url=f"/leads/pending_review?msg={msg}&opp={opp_id}", status_code=303)


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
