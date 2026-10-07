"""Mənbələr arası dublikat: eyni satınalma bir `tenders` sətri, onu tapan
bütün mənbələrin lead-ləri ona bağlanır (sxem: "CRM baza sxemi", addım 2-3).

Uyğunluq sırası (ilk tapılan qalib):
  1. eyni istinad nömrəsi (etender eventId, bp EDP nömrəsi, Dünya Bankı ID-si);
  2. eyni barmaq izi (normal təşkilat adı + normal başlıq) VƏ eyni son tarix
     (±1 gün; hər ikisində yoxdursa — son 60 gündə yaradılmış tender).
Tapılsa, yeni lead `duplicate` olur və `duplicate_of` tenderin ilk lead-inə
işarə edir — lead silinmir, hansı mənbələrin tapdığı görünür.

Bilinən hədd: fed.az məqaləsi ilə etender elanının başlıqları tam fərqlidir
("Dövlət qurumu keytring xidməti alır" / "Tədbir ... (keytrinq) xidmətlərinin
satın alınması") — bunu deterministik qayda tutmur (Faza 2: AI müqayisəsi).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeadRecord, Organization, Tender
from app.scrapers.base import fold

log = structlog.get_logger()

MATCH_WINDOW = timedelta(days=60)
DEADLINE_TOLERANCE = timedelta(days=1)
MIN_TITLE_WORDS = 3  # daha qısa başlıq ("Kabel") müxtəlif tenderlərdə təkrarlanır

_QUOTES = re.compile(r"[\"'«»“”„`’]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_VOEN = re.compile(r"VÖEN\s*[:\-]?\s*(\d{10})")
_ORG_BEFORE_VOEN = re.compile(r"(?:^|\n)([^\n(]{5,150}?)\s*\(\s*VÖEN", re.UNICODE)

_LEGAL_FORMS = (
    "məhdud məsuliyyətli cəmiyyəti", "məhdud məsuliyyətli cəmiyyət", "açıq səhmdar cəmiyyəti",
    "qapalı səhmdar cəmiyyəti", "səhmdar cəmiyyəti", "publik hüquqi şəxs", "istehsalat birliyi",
)
_LEGAL_ABBREVIATIONS = {"mmc", "asc", "qsc", "phş", "ib", "llc", "ltd", "gmbh"}
# Başlıqda tenderi fərqləndirməyən ifadələr — sıra vacibdir (uzun əvvəl).
_TITLE_NOISE = (
    "kotirovka sorğusu haqqında bildiriş", "açıq tender elan edir", "tender elanı", "tender nəticəsi",
    "satın alınması məqsədilə keçirilən", "satın alınması üzrə", "satın alınması", "satınalınması",
    "ilə bağlı", "məqsədilə", "keçirilən", "üzrə", "haqqında", "bildiriş", "elan edir", "tender",
)
_REF_PATTERNS = {
    "etender": re.compile(r"/competition/detail/(\d+)"),
    "worldbank": re.compile(r"/procurement-detail/([A-Za-z0-9]+)"),
    "bp_edp": re.compile(r"#(EDP-[\w/-]+)$"),
}


def _clean(text: str) -> str:
    text = _QUOTES.sub(" ", fold(text))
    return _SPACES.sub(" ", _NON_WORD.sub(" ", text)).strip()


def normalize_org(name: str | None) -> str:
    if not name:
        return ""
    text = _QUOTES.sub(" ", fold(name))
    for form in _LEGAL_FORMS:
        text = text.replace(fold(form), " ")
    words = [w for w in _clean(text).split() if w not in _LEGAL_ABBREVIATIONS]
    return " ".join(words)


def normalize_title(title: str) -> str:
    text = _QUOTES.sub(" ", fold(title))
    for noise in _TITLE_NOISE:
        text = text.replace(fold(noise), " ")
    return _clean(text)


def fingerprint(org: str | None, title: str) -> str | None:
    norm_title = normalize_title(title)
    if len(norm_title.split()) < MIN_TITLE_WORDS:
        return None
    return hashlib.sha256(f"{normalize_org(org)}|{norm_title}".encode("utf-8")).hexdigest()


_REF_SOURCE_ALIASES = {"etender_construction": "etender"}  # eyni portal: eyni tender dublikat olsun


def reference_for(source: str, url: str) -> str | None:
    source = _REF_SOURCE_ALIASES.get(source, source)
    pattern = _REF_PATTERNS.get(source)
    m = pattern.search(url) if pattern else None
    return f"{source}:{m[1]}" if m else None


def extract_voen(text: str | None) -> str | None:
    m = _VOEN.search(text or "")
    return m[1] if m else None


def organization_for(session: Session, name: str | None, voen: str | None) -> Organization | None:
    """VÖEN varsa onunla, yoxdursa normal adla tap; tapılmasa yarat."""
    norm = normalize_org(name)
    org = None
    if voen:
        org = session.execute(select(Organization).where(Organization.voen == voen)).scalar_one_or_none()
    if org is None and norm:
        org = session.execute(
            select(Organization).where(Organization.name_normalized == norm).limit(1)
        ).scalar_one_or_none()
    if org is None and (norm or voen):
        org = Organization(name=(name or f"VÖEN {voen}").strip(), name_normalized=norm, voen=voen)
        session.add(org)
        session.flush()
    elif org is not None and voen and not org.voen:
        org.voen = voen
    return org


def _deadlines_match(a: datetime | None, b: datetime | None, created: datetime, now: datetime) -> bool:
    if a and b:
        return abs(a - b) <= DEADLINE_TOLERANCE
    if a is None and b is None:
        return now - created <= MATCH_WINDOW
    return False  # birində var, digərində yox — eyni olduğuna əmin deyilik


def _find_tender(session: Session, ref: str | None, fp: str | None, deadline: datetime | None) -> Tender | None:
    if ref:
        tender = session.execute(select(Tender).where(Tender.reference_no == ref).limit(1)).scalar_one_or_none()
        if tender:
            return tender
    if fp:
        now = datetime.utcnow()
        for tender in session.execute(select(Tender).where(Tender.fingerprint == fp)).scalars():
            if _deadlines_match(tender.deadline_at, deadline, tender.created_at, now):
                return tender
    return None


def link_tender(session: Session, record: LeadRecord) -> bool:
    """Tender lead-ini tenderə bağlayır. Dublikatdırsa True (status `duplicate` olur)."""
    if record.category != "tender" or record.tender_id is not None:
        return False

    voen = extract_voen(record.raw_text)
    org_name = record.entity_name
    if not org_name and voen:
        m = _ORG_BEFORE_VOEN.search(record.raw_text)
        org_name = m[1].strip() if m else None
    org = organization_for(session, org_name, voen)
    if org is not None:
        record.organization_id = org.id

    ref = reference_for(record.source, record.source_url)
    fp = fingerprint(org_name, record.title)
    tender = _find_tender(session, ref, fp, record.deadline_at)

    if tender is None:
        tender = Tender(
            organization_id=org.id if org else None, title=record.title, reference_no=ref,
            published_at=record.posted_at, deadline_at=record.deadline_at, fingerprint=fp,
        )
        session.add(tender)
        session.flush()
        record.tender_id = tender.id
        return False

    record.tender_id = tender.id
    tender.deadline_at = tender.deadline_at or record.deadline_at
    tender.reference_no = tender.reference_no or ref
    tender.organization_id = tender.organization_id or (org.id if org else None)

    primary = session.execute(
        select(LeadRecord.dedupe_key)
        .where(LeadRecord.tender_id == tender.id, LeadRecord.status != "duplicate",
               LeadRecord.dedupe_key != record.dedupe_key)
        .order_by(LeadRecord.scraped_at)
        .limit(1)
    ).scalar_one_or_none()
    if primary is None:
        return False
    record.status = "duplicate"
    record.duplicate_of = primary
    log.info("cross_source_duplicate", source=record.source, url=record.source_url,
             duplicate_of=primary, tender_id=tender.id)
    return True
