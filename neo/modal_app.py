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

from datetime import datetime
from zoneinfo import ZoneInfo

import modal
from fastapi import Header

TICK_MINUTES = 10

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


@app.function(schedule=modal.Cron(f"*/{TICK_MINUTES} * * * *"), timeout=120)
def tick() -> list[str]:
    from app.scheduler import due_sources

    now = datetime.now(ZoneInfo("Asia/Baku")).replace(second=0, microsecond=0)
    # Cron bir az gec başlaya bilər — pəncərəni 10 dəqiqəlik sərhədə yuvarlaqlaşdır.
    now = now.replace(minute=now.minute - now.minute % TICK_MINUTES)
    keys = due_sources(now, TICK_MINUTES)
    for key in keys:
        scrape_source.spawn(key, "schedule")
    print(f"tick {now:%H:%M} → {keys or 'heç nə'}")
    return keys


@app.function(timeout=60)
@modal.fastapi_endpoint(method="POST", docs=False)
def trigger(payload: dict, authorization: str | None = Header(default=None)):
    from fastapi.responses import JSONResponse

    from app import triggers
    from app.scheduler import LOCAL_ONLY, SOURCE_TASKS

    local = triggers.local_queue()

    def spawn(key: str, trig: str) -> None:
        # Modal-ı bloklayan mənbələr Mac worker-ə gedir (Queue), qalanı burada fonda.
        if key in LOCAL_ONLY:
            local.put((key, trig))
        else:
            scrape_source.spawn(key, trig)

    status, body = triggers.handle(payload, authorization, set(SOURCE_TASKS), spawn)
    return JSONResponse(body, status_code=status)
