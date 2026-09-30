"""Mənbə başına Redis kilidi — eyni mənbə eyni vaxtda iki dəfə işləməsin.

Planlı scrape (Celery worker) və paneldən "Scrape indi" ayrı proseslərdədir —
panelin yaddaşdakı `_running` dəsti worker-i görmür. Kilid Redis-dədir, hər
iki tərəf onu götürür. Redis əlçatan deyilsə kilid "götürülmüş" sayılır (scrape
dayanmır — ən pis halda köhnə davranış: iki eyni işləmə, dublikatları dedup tutur).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import structlog

log = structlog.get_logger()

LOCK_TIMEOUT_S = 60 * 60  # ən uzun mənbə (etender + AI) bir neçə dəqiqədir; ilişən kilid 1 saata açılır


def _redis():
    import redis

    return redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                                socket_connect_timeout=1, socket_timeout=2)


@contextmanager
def source_lock(source_key: str) -> Iterator[bool]:
    """`with source_lock(key) as acquired:` — acquired=False: başqa proses artıq işlədir."""
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
