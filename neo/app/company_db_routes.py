"""Şirkət bazası: analiz olunmuş / əlavə edilmiş şirkətlər, şirkət kartı və əməkdaşlıq razılaşmaları
(app/companies.py). Analizin özü ayrıca səhifədədir — /tools/company."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select

from app import companies, company_analysis, employees
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.contacts import EMAIL_RE
from app.crm import BAKU_OFFSET, STAGE_NAMES, today
from app.crm_routes import _go, _render
from app.db.models import ClientContract, Opportunity, Organization, Reminder, User
from app.leaders import enabled as employees_enabled

router = APIRouter()


@router.get("/companies")
def companies_page(request: Request, q: str = "", show: str = "all", page: int = 1,
                   me: User = Depends(REQUIRE["sales"])):
    show = show if show in companies.LIST_FILTERS else "all"
    with security.SessionLocal() as db:
        rows, total = companies.list_companies(db, q, show, page)
        counts = companies.counts(db)
    pages = max(1, -(-total // companies.PAGE_SIZE))
    return _render(request, "company_db.html", {
        "active": "company_db", "rows": rows, "total": total, "counts": counts, "q": q.strip()[:100],
        "show": show, "page": min(max(page, 1), pages), "pages": pages,
    })


def _card_context(db, org: Organization) -> dict:
    staff = employees.cached(db, org.name)
    users = {u.id: u.full_name for u in db.scalars(select(User))}
    return {
        "active": "company_db", "org": org, "analysis": companies.analysis_of(org),
        "agreements": companies.agreements_of(db, org.id),
        "opportunities": list(db.scalars(select(Opportunity).where(Opportunity.organization_id == org.id)
                                         .order_by(Opportunity.updated_at.desc()).limit(20))),
        "staff": employees.items_of(staff), "staff_at": staff.created_at if staff else None,
        "employees_ready": employees_enabled(), "users": users, "today": today(),
        "size_names": company_analysis.SIZE_NAMES, "fit_names": company_analysis.FIT_NAMES,
        "status_names": companies.AGREEMENT_STATUS_NAMES, "shown_status": companies.shown_status,
        "stage_names": STAGE_NAMES,
    }


@router.get("/companies/{org_id}")
def company_card(request: Request, org_id: int, me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        org = db.get(Organization, org_id)
        if org is None:
            return _go("/companies", err="Şirkət tapılmadı")
        return _render(request, "company_card.html", _card_context(db, org))


@router.post("/companies/{org_id}", dependencies=[Depends(verify_csrf)])
def company_update(org_id: int, website: str = Form(""), voen: str = Form(""), phone: str = Form(""),
                   email: str = Form(""), industry: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    path = f"/companies/{org_id}"
    voen, email = voen.strip(), email.strip().lower()
    if voen and not companies.VOEN_RE.fullmatch(voen):
        return _go(path, err="VÖEN 10 rəqəmdən ibarət olmalıdır")
    if email and (len(email) > 254 or not EMAIL_RE.fullmatch(email)):
        return _go(path, err="Email formatı yanlışdır")
    with security.SessionLocal() as db:
        org = db.get(Organization, org_id)
        if org is None:
            return _go("/companies", err="Şirkət tapılmadı")
        if voen and voen != org.voen and db.scalar(select(Organization.id).where(Organization.voen == voen)):
            return _go(path, err="Bu VÖEN başqa şirkətdədir")
        domain = companies.normalize_domain(website)
        if website.strip() and not domain:
            return _go(path, err="Sayt ünvanı yanlışdır")
        org.website, org.domain = (companies.site_url(website), domain) if domain else (None, None)
        org.voen = voen or None
        org.phone = phone.strip()[:64] or None
        org.email = email or None
        org.industry = industry.strip()[:300] or None
        db.commit()
    return _go(path, msg="Yadda saxlandı")


@router.post("/companies/{org_id}/notes", dependencies=[Depends(verify_csrf)])
def company_notes(request: Request, org_id: int, notes: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    """Əlavə qeydlər. JS (analiz səhifəsi, kart) JSON alır — səhifə yenilənmir; JS-siz forma yönləndirir."""
    wants_json = "application/json" in request.headers.get("accept", "")
    path = f"/companies/{org_id}#notes"

    def fail(error: str, status: int):
        return JSONResponse({"error": error}, status_code=status) if wants_json else _go(path, err=error)

    with security.SessionLocal() as db:
        org = db.get(Organization, org_id)
        if org is None:
            return fail("Şirkət tapılmadı", 404)
        try:
            companies.save_notes(org, notes, me.id)
        except companies.CompanyError as exc:
            return fail(str(exc), 400)
        db.commit()
        saved_at = (org.notes_updated_at + BAKU_OFFSET).strftime("%d.%m.%Y %H:%M")
    if wants_json:
        return {"ok": True, "saved_at": saved_at, "by": me.full_name}
    return _go(path, msg="Qeyd yadda saxlandı")


# --- Razılaşmalar ----------------------------------------------------------------------


@router.post("/companies/{org_id}/agreements", dependencies=[Depends(verify_csrf)])
def agreement_add(org_id: int, title: str = Form(""), description: str = Form(""), starts_on: str = Form(""),
                  ends_on: str = Form(""), status: str = Form("active"), contact: str = Form(""),
                  me: User = Depends(REQUIRE["sales"])):
    path = f"/companies/{org_id}"
    with security.SessionLocal() as db:
        org = db.get(Organization, org_id)
        if org is None:
            return _go("/companies", err="Şirkət tapılmadı")
        try:
            row = companies.add_agreement(db, org, companies.parse_agreement(
                title, description, starts_on, ends_on, status, contact), me.id)
        except companies.CompanyError as exc:
            return _go(path + "#agreements", err=str(exc))
        db.commit()
        return _go(f"{path}#a{row.id}", msg="Razılaşma əlavə olundu")


def _own(db, aid: int, me: User) -> tuple[ClientContract | None, str | None]:
    row = db.get(ClientContract, aid)
    if row is None:
        return None, "Razılaşma tapılmadı"
    if row.created_by_id != me.id and not security.has_role(me, "manager"):
        return None, "Başqasının əlavə etdiyi razılaşmanı yalnız menecer dəyişə bilər"
    return row, None


def _back(row: ClientContract | None, back: str) -> str:
    if back.startswith("/tools/contracts"):
        return back
    return f"/companies/{row.organization_id}" if row is not None and row.organization_id else "/companies"


@router.post("/agreements/{aid}", dependencies=[Depends(verify_csrf)])
def agreement_update(aid: int, title: str = Form(""), description: str = Form(""), starts_on: str = Form(""),
                     ends_on: str = Form(""), status: str = Form("active"), contact: str = Form(""),
                     back: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        row, error = _own(db, aid, me)
        path = _back(row or db.get(ClientContract, aid), back)
        if error:
            return _go(path, err=error)
        try:
            companies.update_agreement(row, companies.parse_agreement(
                title, description, starts_on, ends_on, status, contact))
        except companies.CompanyError as exc:
            return _go(f"{path}#a{aid}", err=str(exc))
        db.commit()
    return _go(f"{path}#a{aid}", msg="Razılaşma yeniləndi")


@router.post("/agreements/{aid}/delete", dependencies=[Depends(verify_csrf)])
def agreement_delete(aid: int, back: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        row, error = _own(db, aid, me)
        path = _back(row or db.get(ClientContract, aid), back)
        if error:
            return _go(path, err=error)
        db.execute(delete(Reminder).where(Reminder.contract_id == aid))
        db.delete(row)
        db.commit()
    return _go(path, msg="Razılaşma silindi")
