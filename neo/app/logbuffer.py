"""Paneldəki "Loglar" səhifəsi üçün son log yazıları (yaddaşda).

Bütün structlog hadisələri (scraper, pipeline, AI) və hər HTTP sorğusu (httpx:
sayt, robots.txt, Apify, OpenAI) buraya da yazılır. Hər yazı işləyən mənbə ilə
etiketlənir — `bind_source()` thread-in kontekstinə mənbə açarını (`run`) qoyur, həmin thread-dəki
bütün loglar (httpx daxil) onu daşıyır. Server yenidən başlayanda buffer sıfırlanır;
terminal çıxışı (ConsoleRenderer) əvvəlki kimi qalır.
"""
from __future__ import annotations

import itertools
import logging
import re
import secrets
import sys
from collections import deque
from datetime import datetime

import structlog

MAX_ENTRIES = 5000

_entries: deque[dict] = deque(maxlen=MAX_ENTRIES)
_ids = itertools.count(1)
# Hər proses başlanğıcında yeni — id-lər yenidən 1-dən başlayanda brauzer köhnə `after`-i atsın.
BOOT_ID = secrets.token_hex(4)

# LinkedIn sorğusunun URL-ində Apify tokeni var (?token=...) — panelə çıxmasın.
_SECRET_RE = re.compile(r"((?:token|api_key|apikey|key|password)=)[^&\s\"']+", re.IGNORECASE)
_SKIP_FIELDS = {"event", "level", "timestamp", "run", "exc_info", "stack_info"}


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1***", text)


def _add(level: str, event: str, source: str | None, details: str) -> None:
    _entries.append({
        "id": next(_ids),
        "ts": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "event": event,
        "source": source or "—",
        "details": _redact(details),
    })


def _capture(logger, method_name, event_dict):
    details = " ".join(f"{k}={v}" for k, v in event_dict.items() if k not in _SKIP_FIELDS)
    if event_dict.get("exc_info"):
        exc_type, exc, _ = sys.exc_info()
        if exc_type:
            details += f" error={exc_type.__name__}: {exc}"
    # "run" = paneldəki mənbə açarı (fed_tenders); "source" = scraper-in öz adı (fed).
    source = event_dict.get("run") or event_dict.get("source")
    _add(event_dict.get("level", method_name), str(event_dict.get("event")), source, details)
    return event_dict


class _StdlibHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        source = structlog.contextvars.get_contextvars().get("run")
        _add(record.levelname.lower(), record.name, source, record.getMessage())


def setup() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            _capture,
            structlog.dev.ConsoleRenderer(),
        ]
    )
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.INFO)
    if not any(isinstance(h, _StdlibHandler) for h in httpx_logger.handlers):
        httpx_logger.addHandler(_StdlibHandler())


def bind_source(source_key: str) -> None:
    structlog.contextvars.bind_contextvars(run=source_key)


def unbind_source() -> None:
    structlog.contextvars.unbind_contextvars("run")


def entries(after: int = 0, source: str | None = None, level: str | None = None) -> list[dict]:
    return [
        e for e in list(_entries)
        if e["id"] > after
        and (not source or e["source"] == source)
        and (not level or e["level"] == level)
    ]
