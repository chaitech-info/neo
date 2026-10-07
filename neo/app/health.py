"""Mənbə sağlamlığı — dashboard-dakı "Vəziyyət" sütunu.

Son 7 günün `scrape_runs`-undan hər mənbə üçün bir hökm:
- error — son 3 run-ın hamısı uğursuz (məs. 1news: sayt bölməni silib, 404);
- stale — cədvələ görə çoxdan işləməli idi, işləməyib (məs. Mac sönülüdür,
  LOCAL_ONLY mənbələr dayanıb);
- empty — son 3 uğurlu run saytdan heç nə gətirməyib (`items_fetched` = 0):
  selector sınmış ola bilər — xəta yoxdur, amma heç nə də yoxdur;
- ok.
`items_seen` (filtrdən keçən) 0 olması normaldır — worldbank həftələrlə uyğun
elan tapmaya bilər; ona görə "boş" yalnız filtrdən ƏVVƏLKİ sayla yoxlanır.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ScrapeRun

WINDOW = timedelta(days=7)
STREAK = 3

LABELS = {"ok": "OK", "error": "Xəta", "stale": "Gecikir", "empty": "Boş"}


@dataclass
class Health:
    state: str                       # ok | error | stale | empty
    detail: str                      # tooltip: say, son uğur, son xəta
    runs: int = 0
    failed: int = 0

    @property
    def label(self) -> str:
        return LABELS[self.state]


def expected_gap(cron) -> timedelta:
    """Celery crontab → iki run arası maksimum gözlənilən fasilə (2 qat + 1 saat ehtiyat)."""
    runs_per_day = max(len(cron.hour), 1)
    return timedelta(hours=2 * 24 / runs_per_day + 1)


def source_health(session: Session, crons: dict[str, object], now: datetime | None = None) -> dict[str, Health]:
    now = now or datetime.utcnow()
    rows = session.scalars(
        select(ScrapeRun).where(ScrapeRun.started_at >= now - WINDOW).order_by(ScrapeRun.started_at.desc())
    ).all()
    by_source: dict[str, list[ScrapeRun]] = {}
    for run in rows:
        by_source.setdefault(run.source_key, []).append(run)

    out = {}
    for key, cron in crons.items():
        runs = [r for r in by_source.get(key, []) if r.status != "running"]
        failed = [r for r in runs if r.status == "failed"]
        ok = [r for r in runs if r.status == "ok"]
        last = runs[0].started_at if runs else None

        parts = [f"7 gün: {len(runs)} run, {len(failed)} xəta"]
        if ok:
            parts.append(f"son uğurlu: {_fmt(ok[0].started_at)}")
        if failed:
            parts.append(f"son xəta: {_short_error(failed[0].error)}")

        if runs[:STREAK] and all(r.status == "failed" for r in runs[:STREAK]):
            state = "error"
        elif last is None or now - last > expected_gap(cron):
            state = "stale"
            parts.insert(0, "son run: " + (_fmt(last) if last else "7 gündə heç biri"))
        elif len(ok) >= STREAK and all(r.items_fetched == 0 for r in ok[:STREAK]):
            state = "empty"
            parts.insert(0, f"son {STREAK} run saytdan 0 elan gətirib — selector yoxlanmalıdır")
        else:
            state = "ok"
        out[key] = Health(state, " · ".join(parts), len(runs), len(failed))
    return out


def _short_error(error: str | None) -> str:
    """Panel üçün qısa xəta. Baza xətalarında host/SQL göstərilmir."""
    first = (error or "").splitlines()[0] if error else ""
    if "psycopg2" in first or "OperationalError" in first or "DatabaseError" in first:
        return "baza bağlantısı kəsildi (Neon)"
    return first[:120]


def _fmt(value: datetime) -> str:
    return (value + timedelta(hours=4)).strftime("%d.%m %H:%M")  # UTC → Bakı
