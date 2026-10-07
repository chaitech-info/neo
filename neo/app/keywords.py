"""Açar sözlər və maraq sahələri — bazadan (paneldə redaktə olunur).

Scraper-lər (BaseScraper.select_relevant) və AI (ai_filter, ai_triage) bunları
buradan oxuyur. Nəticə 60 saniyə keşlənir: panelin öz prosesi dəyişiklikdən
sonra keşi dərhal təmizləyir (`invalidate`), Celery worker isə ən gec bir
dəqiqəyə yeni siyahını görür.

Baza əlçatan deyilsə və ya hələ boşdursa (miqrasiya olunmayıb) — None qaytarılır
və çağıran tərəf koddakı siyahılarla davam edir (heç nə dayanmır).
Bax docs/knowledge-base/04-decisions.md → "Açar sözlər bazadan".
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.scrapers.base import fold

log = structlog.get_logger()

CACHE_TTL = 60.0
AI_HINT_TERMS_PER_AREA = 20

# kind (select_relevant) → hansı `applies_to` dəyərləri keçərlidir.
SCOPES = {
    "tender": ("all", "tender"),
    "news": ("all", "news"),
    "jobs": ("all", "jobs"),
}


@dataclass(frozen=True)
class Term:
    text: str
    match: str = "prefix"  # prefix | word | phrase


@dataclass
class Area:
    code: str
    name: str
    priority: int
    ai_description: str | None
    terms: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    positive: dict[str, list[Term]]   # kind → terminlər
    negative: dict[str, list[Term]]
    areas: list[Area]


_lock = threading.Lock()
_cache: dict = {"at": 0.0, "snap": None}


def _session() -> Session:
    from app.db.engine import engine  # gec import: scraper modulları bazasız da yüklənsin

    return Session(engine)


def _load() -> Snapshot | None:
    from app.db.models import InterestArea, Keyword

    try:
        with _session() as s:
            areas = list(s.scalars(select(InterestArea).where(InterestArea.is_active.is_(True))
                                   .order_by(InterestArea.priority, InterestArea.id)))
            rows = s.execute(
                select(Keyword, InterestArea.code)
                .join(InterestArea, InterestArea.id == Keyword.interest_area_id)
                .where(Keyword.is_active.is_(True), InterestArea.is_active.is_(True))
            ).all()
    except Exception:
        log.warning("keywords_db_unavailable", exc_info=True)
        return None
    if not rows:
        return None

    positive = {k: [] for k in SCOPES}
    negative = {k: [] for k in SCOPES}
    by_area: dict[str, list[str]] = {}
    for kw, area_code in rows:
        term = Term(kw.term.strip(), kw.match)
        if not term.text:
            continue
        for kind, scopes in SCOPES.items():
            if kw.applies_to in scopes:
                (negative if kw.is_negative else positive)[kind].append(term)
        if not kw.is_negative:
            by_area.setdefault(area_code, []).append(term.text)
    return Snapshot(
        positive=positive,
        negative=negative,
        areas=[Area(a.code, a.name, a.priority, a.ai_description, by_area.get(a.code, [])) for a in areas],
    )


def snapshot() -> Snapshot | None:
    with _lock:
        if _cache["snap"] is not None and time.monotonic() - _cache["at"] < CACHE_TTL:
            return _cache["snap"]
    snap = _load()
    with _lock:
        _cache["snap"], _cache["at"] = snap, time.monotonic()
    return snap


def invalidate() -> None:
    with _lock:
        _cache["snap"], _cache["at"] = None, 0.0


def terms_for(kind: str) -> tuple[list[Term], list[Term]] | None:
    """(müsbət, mənfi) terminlər; baza yoxdursa None."""
    snap = snapshot()
    if snap is None:
        return None
    return snap.positive.get(kind, []), snap.negative.get(kind, [])


def matches(text: str, term: Term) -> bool:
    """prefix: sözün əvvəli ("qidalanma" → "qidalanması"); word: tam söz;
    phrase: ifadə sözün əvvəlindən başlayır (çoxsözlü terminlər)."""
    t, k = fold(text), fold(term.text)
    tail = r"(?!\w)" if term.match == "word" else ""
    return re.search(r"(?<!\w)" + re.escape(k) + tail, t) is not None


def any_match(text: str, terms: list[Term]) -> bool:
    return any(matches(text, term) for term in terms)


def company_profile() -> str | None:
    """AI promptları üçün şirkət profili — aktiv maraq sahələri, prioritetə görə."""
    snap = snapshot()
    if snap is None or not snap.areas:
        return None
    lines = ["Şirkətin maraq sahələri (prioritet 1 = əsas):"]
    for a in snap.areas:
        lines.append(f"- [{a.priority}] {a.name}: {a.ai_description or ''}".rstrip(": "))
        if a.terms:
            lines.append(f"  Siqnal sözləri: {', '.join(a.terms[:AI_HINT_TERMS_PER_AREA])}")
    return "\n".join(lines)
