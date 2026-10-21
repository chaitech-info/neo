"""Scraping Modal-da (modal.com) — Celery + Redis + ayrıca worker maşını əvəzinə.

- `scrape_source`: bir mənbəni işlədir (pipeline → Neon). Kilid modal.Dict-də.
- `tick`: 10 dəqiqədən bir cron — app/scheduler.py-dakı beat_schedule-a görə
  vaxtı çatan mənbələri fonda başladır. (Starter planda 5 cron limiti var —
  hər mənbəyə ayrıca cron yerinə bir "dispatcher".)
- `trigger`: web endpoint — Vercel panelinin "Scrape indi" düyməsi bura POST edir
  (app/triggers.py). Bearer TRIGGER_TOKEN ilə qorunur.
- Modal IP-lərini bloklayan mənbələr (app/scheduler.py::LOCAL_ONLY — etender, fed, report,
  fed_tenders, oxu) burada işləmir: onları Mac-dakı Celery worker işlədir
  (NEO_SCRAPE_ROLE=local), "Scrape indi" sorğuları ona modal.Queue ilə çatır.

Secret `neo-secrets`: DATABASE_URL (Neon pooled), OPENAI_API_KEY, AI_MODEL,
APIFY_TOKEN, TRIGGER_TOKEN. Deploy: `modal deploy modal_app.py` (bax 05-runbook.md).
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import modal
from fastapi import Header

TICK_MINUTES = 10
# Modal cron-u deploy vaxtı seçilir. Workspace-in 5 cron limiti dolubsa (2026-10-14: emilabdurahmanli05
# workspace-i — flutter-study-bot, panelflo-bot) `NEO_MODAL_CRON=off modal deploy modal_app.py`:
# tick-i GitHub Actions çağırır (.github/workflows/neo-tick.yml → trigger {"tick": true}).
MODAL_CRON = os.environ.get("NEO_MODAL_CRON", "on") != "off"
TICK_STATE = "neo-tick-state"   # modal.Dict: son tick vaxtı — gecikən/buraxılan çağırışları tutmaq üçün
MAX_CATCHUP_MIN = 24 * 60  # GitHub cron-u bəzən 4-5 saat gecikir (2026-10-16 ölçüsü) — gün ərzində heç nə buraxılmasın

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements.txt")
    .env({"NEO_LOCK_BACKEND": "modal", "NEO_SCRAPE_ROLE": "modal", "PYTHONUNBUFFERED": "1"})
    .add_local_dir("app", "/root/app", ignore=["**/__pycache__/**", "templates/**", "static/**"])
)
secrets = [modal.Secret.from_name("neo-secrets")]
app = modal.App("neo-scraper", image=image, secrets=secrets)


@app.function(timeout=60 * 60, retries=0)
def scrape_source(source_key: str, trigger: str = "schedule") -> int | None:
    from app.scheduler import scrape_source as task

    return task.run(source_key, trigger)  # Celery tapşırığının gövdəsi, broker-siz


def catchup_window(now: datetime, last: str | None) -> int:
    """Son tick-dən bəri keçən dəqiqələr (ilk dəfə TICK_MINUTES, ən çox MAX_CATCHUP_MIN). 0 = bu dəqiqə
    artıq işlənib. GitHub Actions cron-u gecikir və bəzən buraxır — pəncərə üst-üstə düşmür, boşluq qalmır."""
    if not last:
        return TICK_MINUTES
    gap = int((now - datetime.fromisoformat(last)).total_seconds() // 60)
    return 0 if gap <= 0 else min(gap, MAX_CATCHUP_MIN)


@app.function(timeout=300, retries=0)
def poll_mail() -> int:
    """Göndərilmiş məktublara gələn cavablar (app/mailboxes.py) — şirkət poçtlarının şifrələri
    (SMTP_PASSWORD, MAILBOX_<KEY>_PASSWORD) neo-secrets-də olmalıdır."""
    from sqlalchemy.orm import sessionmaker

    from app import mailboxes
    from app.db.engine import engine

    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        added, errors = mailboxes.poll_all(db)
    print(f"poll_mail: {added} yeni cavab" + (f", xəta: {errors}" if errors else ""))
    return added


@app.function(schedule=modal.Cron(f"*/{TICK_MINUTES} * * * *") if MODAL_CRON else None, timeout=120)
def tick() -> list[str]:
    from app.scheduler import due_sources

    if os.environ.get("NEO_MAIL_POLL", "on") != "off":
        poll_mail.spawn()
    now = datetime.now(ZoneInfo("Asia/Baku")).replace(second=0, microsecond=0)
    state = modal.Dict.from_name(TICK_STATE, create_if_missing=True)
    window = catchup_window(now, state.get("last"))
    if not window:
        return []
    keys = due_sources(now, window)
    state["last"] = now.isoformat()
    for key in keys:
        scrape_source.spawn(key, "schedule")
    print(f"tick {now:%H:%M} (son {window} dəq) → {keys or 'heç nə'}")
    return keys


@app.function(timeout=60)
@modal.fastapi_endpoint(method="POST", docs=False)
def trigger(payload: dict, authorization: str | None = Header(default=None)):
    from fastapi.responses import JSONResponse

    from app import triggers
    from app.scheduler import LOCAL_ONLY, SOURCE_TASKS
    from app.sources import az_proxy

    def spawn(key: str, trig: str) -> None:
        # Modal-ı bloklayan mənbələr: AZ proxy varsa burada (proxy ilə), yoxdursa Mac worker-ə (Queue).
        if key in LOCAL_ONLY and not az_proxy():
            triggers.local_queue().put((key, trig))
        else:
            scrape_source.spawn(key, trig)

    status, body = triggers.handle(payload, authorization, set(SOURCE_TASKS), spawn, on_tick=tick.spawn)
    return JSONResponse(body, status_code=status)
