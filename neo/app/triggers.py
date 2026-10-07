"""Paneldən "Scrape indi" → Modal (scraping orada işləyir, bax modal_app.py).

Panel Vercel-dədir: funksiya cavabdan sonra fon işi saxlaya bilmir, uzun mənbələr
isə vaxt limitini keçir. Ona görə panel Modal-dakı `trigger` web endpoint-inə
POST göndərir, o da `scrape_source`-u fonda başladır (`.spawn`) və dərhal qayıdır.

Env (hər iki tərəfdə eyni TRIGGER_TOKEN):
- Vercel: MODAL_TRIGGER_URL (endpoint ünvanı), TRIGGER_TOKEN
- Modal: TRIGGER_TOKEN (neo-secrets secret-i)

Modal-ı bloklayan mənbələr (scheduler.LOCAL_ONLY) Modal-da işləmir: endpoint
onları `modal.Queue` LOCAL_QUEUE-yə qoyur, Mac-dakı Celery worker
(NEO_SCRAPE_ROLE=local) `start_local_bridge` ilə oradan götürür. Mac-da modal
paketi və `MODAL_PROFILE=neo` (.env) lazımdır.
"""
from __future__ import annotations

import hmac
import os
import queue as stdqueue
import threading
import time
from typing import Callable

import httpx
import structlog

log = structlog.get_logger()

TRIGGERS = ("manual", "scrape_all")
LOCAL_QUEUE = "neo-local-requests"
BLOCK_S = 50   # Queue.get gözləmə müddəti
RETRY_S = 30   # xətadan sonra fasilə


def request(keys: list[str], trigger: str) -> int:
    """Panel tərəfi. Modal-a göndərir, növbəyə qoyulan mənbə sayını qaytarır. Xəta olsa atır."""
    url, token = os.environ.get("MODAL_TRIGGER_URL"), os.environ.get("TRIGGER_TOKEN")
    if not url or not token:
        raise RuntimeError("MODAL_TRIGGER_URL və ya TRIGGER_TOKEN təyin edilməyib")
    r = httpx.post(url, json={"sources": keys, "trigger": trigger},
                   headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    return int(r.json()["queued"])


def handle(payload: dict, authorization: str | None, known: set[str],
           spawn: Callable[[str, str], None]) -> tuple[int, dict]:
    """Modal tərəfi (endpoint-in məntiqi, test üçün ayrıca). (status, cavab)."""
    token = os.environ.get("TRIGGER_TOKEN") or ""
    if not token or not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        return 401, {"error": "unauthorized"}
    trigger = payload.get("trigger")
    sources = payload.get("sources")
    if trigger not in TRIGGERS or not isinstance(sources, list):
        return 400, {"error": "bad request"}
    queued = 0
    for key in dict.fromkeys(sources):          # təkrarları at, sıranı saxla
        if isinstance(key, str) and key in known:
            spawn(key, trigger)
            queued += 1
        else:
            log.warning("trigger_unknown_source", source=str(key)[:40])
    log.info("trigger_queued", count=queued, trigger=trigger)
    return 200, {"queued": queued}


def local_queue():
    import modal

    return modal.Queue.from_name(LOCAL_QUEUE, create_if_missing=True)


def _bridge_loop(dispatch: Callable[[str, str], None]) -> None:
    queue = None
    while True:
        try:
            if queue is None:
                queue = local_queue()
                log.info("local_bridge_connected", queue=LOCAL_QUEUE)
            try:
                key, trigger = queue.get(timeout=BLOCK_S)
            except stdqueue.Empty:   # 50 san. heç nə gəlmədi — normal hal
                continue
            log.info("local_request_received", source=key, trigger=trigger)
            dispatch(key, trigger)
        except Exception:
            log.warning("local_bridge_error", exc_info=True)
            queue = None
            time.sleep(RETRY_S)


def start_local_bridge(dispatch: Callable[[str, str], None]) -> None:
    """Mac worker tərəfi — fon thread-i."""
    threading.Thread(target=_bridge_loop, args=(dispatch,), name="local-bridge", daemon=True).start()
