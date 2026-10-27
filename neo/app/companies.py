"""Şirkət bazası: analiz olunmuş və ya əl ilə əlavə edilmiş şirkətlər + əməkdaşlıq razılaşmaları.

Şirkətin özü `organizations` cədvəlidir (fürsətlər, tenderlər, analiz — eyni şirkət). Dublikat
yoxlaması: VÖEN → sayt domeni → normallaşdırılmış ad (app/tenders.py normalize_org).
Analiz uğurla bitəndə nəticə `organizations.analysis`-ə (JSON) yazılır — `save_analysis`.
Razılaşmalar `client_contracts`-dadır: bir şirkətin bir neçəsi ola bilər.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from urllib.parse import urlparse

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import AGREEMENT_STATUSES, ClientContract, Opportunity, Organization
from app.tenders import normalize_org, organization_for

AGREEMENT_STATUS_NAMES = {"draft": "Layihə", "active": "Qüvvədə", "expired": "Bitib", "terminated": "Ləğv edilib"}
VOEN_RE = re.compile(r"^\d{10}$")


class CompanyError(ValueError):
    """İstifadəçiyə göstərilən xəta (Azərbaycan dilində)."""


def normalize_domain(url: str | None) -> str | None:
    """"https://www.Sahil.az/haqqimizda" → "sahil.az"; domen deyilsə None."""
    url = (url or "").strip().lower()
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else f"http://{url}").hostname or ""
    except ValueError:
        return None
    host = host.removeprefix("www.")
    return host[:255] if "." in host and " " not in host else None


def site_url(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    return url if url.startswith(("http://", "https://")) else "https://" + url


def find_company(session: Session, name: str | None, website: str | None = None,
                 voen: str | None = None) -> Organization | None:
    if voen:
        org = session.scalars(select(Organization).where(Organization.voen == voen)).first()
        if org is not None:
            return org
    domain = normalize_domain(website)
    if domain:
        org = session.scalars(select(Organization).where(Organization.domain == domain)
                              .order_by(Organization.analysed_at.is_(None), Organization.id)).first()
        if org is not None:
            return org
    norm = normalize_org(name)
    if norm:
        return session.scalars(select(Organization).where(Organization.name_normalized == norm)
                               .order_by(Organization.analysed_at.is_(None), Organization.id)).first()
    return None


def company_for(session: Session, name: str, website: str | None = None, voen: str | None = None) -> Organization:
    """Şirkəti tap (VÖEN → domen → ad), yoxdursa yarat; boş sahələri (sayt, domen, VÖEN) doldurur."""
    name = " ".join((name or "").split())[:300]
    voen = (voen or "").strip() or None
    if voen and not VOEN_RE.fullmatch(voen):
        raise CompanyError("VÖEN 10 rəqəmdən ibarət olmalıdır")
    org = find_company(session, name, website, voen)
    if org is None:
        if len(name) < 2:
            raise CompanyError("Şirkətin adını yaz")
        org = organization_for(session, name, voen)
        if org is None:  # ad yalnız hüquqi formadan ibarətdir ("MMC")
            raise CompanyError("Şirkətin adını tam yaz")
    if voen and not org.voen and not session.scalar(select(Organization.id).where(Organization.voen == voen)):
        org.voen = voen
    domain = normalize_domain(website)
    if domain:
        org.domain = org.domain or domain
        org.website = org.website or site_url(website)
    elif org.website and not org.domain:
        org.domain = normalize_domain(org.website)
    session.flush()
    return org


# --- Analiz ---------------------------------------------------------------------------


def _plain(value):
    if is_dataclass(value):
        return {k: v for k, v in asdict(value).items() if v}
    return value


def save_analysis(session: Session, result: dict, website: str | None, user_id: int | None) -> Organization:
    """Uğurlu analiz → şirkət bazası (dublikat yaratmadan). Əvvəlki analiz yenisi ilə əvəz olunur."""
    org = company_for(session, result["name"], website or result.get("site"))
    ai = result.get("ai") or {}
    facts = result.get("facts") or {}
    org.analysis = json.dumps({
        "name": result["name"], "ai": ai, "site": result.get("site") or "", "site_ok": bool(result.get("site_ok")),
        "found": [_plain(f) for f in result.get("found") or []],
        "facts": {"leads": len(facts.get("leads") or []), "opportunities": facts.get("opportunities") or 0},
    }, ensure_ascii=False)
    industry = (ai.get("industry") or "").strip()
    org.industry = industry[:300] if industry and industry.lower() != "bilinmir" else org.industry
    org.analysis_status = "ok"
    org.analysed_at = datetime.utcnow()
    list_company(org)
    org.analysed_by_id = user_id
    session.commit()
    return org


def analysis_of(org: Organization) -> dict | None:
    if not org.analysis:
        return None
    try:
        data = json.loads(org.analysis)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("ai"), dict) else None


# --- Əlavə qeydlər ----------------------------------------------------------------------

MAX_NOTES = 10000


def save_notes(org: Organization, text: str, user_id: int | None) -> None:
    """Şirkət haqqında sərbəst qeyd (əvvəlki mətn yenisi ilə əvəz olunur; boş — silinir)."""
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) > MAX_NOTES:
        raise CompanyError(f"Qeyd ən çox {MAX_NOTES} simvol ola bilər")
    org.notes = text or None
    org.notes_updated_at = datetime.utcnow()
    org.notes_updated_by_id = user_id
    list_company(org)


# --- Siyahı ---------------------------------------------------------------------------

LIST_FILTERS = ("all", "analysed", "agreements")
PAGE_SIZE = 50


def list_company(org: Organization) -> None:
    """Şirkəti şirkət bazasına sal (analiz edildi, "Şirkət əlavə et" və ya razılaşma əlavə olundu)."""
    org.listed_at = org.listed_at or datetime.utcnow()


def _in_database():
    has_agreement = exists().where(ClientContract.organization_id == Organization.id)
    return Organization.listed_at.is_not(None), has_agreement


def list_companies(session: Session, q: str = "", show: str = "all", page: int = 1) -> tuple[list[tuple], int]:
    """[(şirkət, razılaşma sayı, fürsət sayı)], cəmi — şirkət bazası səhifəsi."""
    member, has_agreement = _in_database()
    stmt = select(Organization).where(member)
    if show == "analysed":
        stmt = stmt.where(Organization.analysed_at.is_not(None))
    elif show == "agreements":
        stmt = stmt.where(has_agreement)
    q = " ".join((q or "").split())[:100]
    if q:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(Organization.name.ilike(needle, escape="\\"),
                              Organization.domain.ilike(needle, escape="\\"),
                              Organization.industry.ilike(needle, escape="\\"),
                              Organization.voen.ilike(needle, escape="\\"),
                              Organization.notes.ilike(needle, escape="\\")))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    agreements = (select(func.count()).select_from(ClientContract)
                  .where(ClientContract.organization_id == Organization.id).correlate(Organization).scalar_subquery())
    opps = (select(func.count()).select_from(Opportunity)
            .where(Opportunity.organization_id == Organization.id).correlate(Organization).scalar_subquery())
    page = max(page, 1)
    rows = session.execute(
        stmt.add_columns(agreements, opps)
        .order_by(func.coalesce(Organization.analysed_at, Organization.listed_at).desc(), Organization.id.desc())
        .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    ).all()
    return [tuple(r) for r in rows], total


def counts(session: Session) -> dict[str, int]:
    member, has_agreement = _in_database()
    count = lambda *cond: session.scalar(select(func.count()).select_from(Organization).where(*cond)) or 0  # noqa: E731
    return {"all": count(member), "analysed": count(member, Organization.analysed_at.is_not(None)),
            "agreements": count(member, has_agreement)}


# --- Razılaşmalar ---------------------------------------------------------------------


def _date(value: str | None, label: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CompanyError(f"{label} yanlışdır")


def parse_agreement(title: str, description: str = "", starts_on: str = "", ends_on: str = "",
                    status: str = "active", contact: str = "") -> dict:
    """Formadan gələn razılaşma → yoxlanmış sahələr (CompanyError)."""
    title = " ".join((title or "").split())[:300]
    if len(title) < 2:
        raise CompanyError("Razılaşmanın adını yaz")
    status = status if status in AGREEMENT_STATUSES else ""
    if not status:
        raise CompanyError("Razılaşmanın statusu yanlışdır")
    start, end = _date(starts_on, "Başlama tarixi"), _date(ends_on, "Bitmə tarixi")
    if start and end and end < start:
        raise CompanyError("Bitmə tarixi başlama tarixindən əvvəl ola bilməz")
    return {"title": title, "note": (description or "").strip()[:4000] or None, "starts_on": start,
            "contract_end": end, "status": status, "contact": (contact or "").strip()[:300] or None}


def add_agreement(session: Session, org: Organization, fields: dict, user_id: int | None) -> ClientContract:
    row = ClientContract(company_name=org.name, organization_id=org.id, created_by_id=user_id,
                         created_at=datetime.utcnow(), **fields)
    list_company(org)
    session.add(row)
    session.flush()
    return row


def update_agreement(row: ClientContract, fields: dict) -> None:
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = datetime.utcnow()


def agreements_of(session: Session, org_id: int) -> list[ClientContract]:
    return list(session.scalars(select(ClientContract).where(ClientContract.organization_id == org_id)
                                .order_by(ClientContract.contract_end.is_(None), ClientContract.contract_end.desc(),
                                          ClientContract.id.desc())))


def shown_status(row: ClientContract, today: date) -> str:
    """"Qüvvədə" olan, amma bitmə tarixi keçmiş razılaşma "Bitib" görünür."""
    if row.status == "active" and row.contract_end and row.contract_end < today:
        return "expired"
    return row.status or "active"
