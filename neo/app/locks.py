"""Mənbə başına Redis kilidi — eyni mənbə eyni vaxtda iki dəfə işləməsin.

Planlı scrape (Celery worker) və paneldən "Scrape indi" ayrı proseslərdədir —
panelin yaddaşdakı `_running` dəsti worker-i görmür. Kilid Redis-dədir, hər
iki tərəf onu götürür. Redis əlçatan deyilsə kilid "götürülmüş" sayılır (scrape
dayanmır — ən pis halda köhnə davranış: iki eyni işləmə, dublikatları dedup tutur).

Modal-da (NEO_LOCK_BACKEND=modal) Redis yoxdur — kilid `modal.Dict`-dədir
(bütün konteynerlər üçün ortaq). Dəyər = götürülmə vaxtı; LOCK_TIMEOUT_S-dən
köhnədirsə (konteyner öldü, buraxılmadı) yenidən götürülür.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Iterator

import structlog

log = structlog.get_logger()

LOCK_TIMEOUT_S = 60 * 60  # ən uzun mənbə (etender + AI) bir neçə dəqiqədir; ilişən kilid 1 saata açılır


def _redis():
    import redis

    return redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                                socket_connect_timeout=1, socket_timeout=2)


MODAL_DICT = "neo-scrape-locks"


def _modal_dict():
    import modal

    return modal.Dict.from_name(MODAL_DICT, create_if_missing=True)


@contextmanager
def _modal_lock(source_key: str) -> Iterator[bool]:
    locks, acquired = None, True
    try:
        locks, now = _modal_dict(), time.time()
        acquired = locks.put(source_key, now, skip_if_exists=True)
        if not acquired and now - float(locks.get(source_key, 0) or 0) > LOCK_TIMEOUT_S:
            locks[source_key] = now
            acquired = True
            log.warning("scrape_lock_stale_taken", source=source_key)
    except Exception:
        log.warning("scrape_lock_unavailable", source=source_key, exc_info=True)
        locks, acquired = None, True
    try:
        yield acquired
    finally:
        if locks is not None and acquired:
            try:
                locks.pop(source_key)
            except Exception:
                log.warning("scrape_lock_release_failed", source=source_key)


@contextmanager
def source_lock(source_key: str) -> Iterator[bool]:
    """`with source_lock(key) as acquired:` — acquired=False: başqa proses artıq işlədir."""
    if os.environ.get("NEO_LOCK_BACKEND") == "modal":
        with _modal_lock(source_key) as acquired:
            yield acquired
        return
    lock = None
    try:
        lock = _redis().lock(f"neo:scrape:{source_key}", timeout=LOCK_TIMEOUT_S, blocking=False)
        acquired = lock.acquire()
    except Exception:
        log.warning("scrape_lock_unavailable", source=source_key)
        lock, acquired = None, True
    try:
        yield acquired
    finally:
        if lock is not None and acquired:
            try:
                lock.release()
            except Exception:
                log.warning("scrape_lock_release_failed", source=source_key)
