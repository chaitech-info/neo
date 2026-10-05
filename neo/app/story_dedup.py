"""Eyni hekayə / eyni şirkət — mənbələr arası dublikat (tender olmayan lead-lər).

Tenderlər app/tenders.py-da (istinad nömrəsi, VÖEN, başlıq barmaq izi) birləşir.
Xəbər və işəgötürən siqnallarında belə identifikator yoxdur: 2026-10-05 analizində
ASELSAN Naxçıvan zavodu 3 dəfə (fed, marja, oxu), Ağdam yağ fabriki 3 dəfə,
"Caucasian Vav Group" boss.az-dan 2 dəfə validasiyaya düşmüşdü.

Qayda (yalnız AI-dan keçən lead-lər üçün, skorlamadan SONRA — təşkilat adı v2
qiymətləndirməsindən gəlir):
- eyni təşkilat (normalize_org) son 14 gündə artıq validasiyada/fürsətdədirsə, və ya
- başlıqların məzmun sözləri ≥75% üst-üstə düşürsə (təşkilat adı yoxdursa),
yeni lead `duplicate` olur, `duplicate_of` ilkinə işarə edir. Satış komandası bir
şirkəti bir dəfə görür; dublikat silinmir, ilkin lead-dən görünür.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeadRecord
from app.scrapers.base import fold
from app.tenders import normalize_org

log = structlog.get_logger()

WINDOW = timedelta(days=14)
TITLE_OVERLAP = 0.75
MIN_TOKENS = 3
STEM = 5  # azərbaycan şəkilçiləri: "Naxçıvanda" / "Naxçıvan" → "naxçı"

_STOP = {"və", "ilə", "üçün", "bu", "bir", "yeni", "foto", "video", "yenilənib", "tender", "elanı",
         "vakansiya", "işçilərinə", "nahar", "verir", "boss", "az", "the", "and", "for", "of"}
_ORG_IN_TITLE = re.compile(r"^(.{2,120}?)\s+—\s+işçilərinə nahar verir")


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"\w+", fold(title))
    return {w[:STEM] for w in words if len(w) > 2 and w not in _STOP and not w.isdigit()}


def overlap(a: set[str], b: set[str]) -> float:
    if len(a) < MIN_TOKENS or len(b) < MIN_TOKENS:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def org_key(record: LeadRecord) -> str:
    name = record.entity_name
    if not name:
        m = _ORG_IN_TITLE.match(record.title or "")
        name = m.group(1) if m else ""
    return normalize_org(name) if name else ""


def find_story_duplicate(session: Session, record: LeadRecord, now: datetime | None = None) -> LeadRecord | None:
    if record.category == "tender" or record.tender_id is not None:
        return None
    now = now or datetime.utcnow()
    candidates = session.scalars(
        select(LeadRecord).where(
            LeadRecord.dedupe_key != record.dedupe_key,
            LeadRecord.status.in_(("pending_review", "converted")),
            LeadRecord.category != "tender",
            LeadRecord.scraped_at >= now - WINDOW,
            LeadRecord.scraped_at <= record.scraped_at,
        ).order_by(LeadRecord.scraped_at)
    ).all()
    key, tokens = org_key(record), title_tokens(record.title or "")
    for other in candidates:
        if key and key == org_key(other):
            return other
        if overlap(tokens, title_tokens(other.title or "")) >= TITLE_OVERLAP:
            return other
    return None


def mark_story_duplicate(session: Session, record: LeadRecord) -> bool:
    primary = find_story_duplicate(session, record)
    if primary is None:
        return False
    record.status, record.duplicate_of = "duplicate", primary.dedupe_key
    session.commit()
    log.info("story_duplicate", source=record.source, title=(record.title or "")[:80],
             duplicate_of=primary.dedupe_key, primary_source=primary.source)
    return True
