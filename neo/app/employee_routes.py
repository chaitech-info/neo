"""Alətlər → Şirkətin işçiləri (LinkedIn, app/employees.py)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from app import employees
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm_routes import _go, _render
from app.db.models import Contact, Opportunity, User
from app.leaders import LeadersError, enabled

router = APIRouter()


def _page(request: Request, *, company: str, opp_id: str, search=None, from_cache=False, limit=employees.DEFAULT_MAX,
          page_error: str | None = None):
    opp, have = None, set()
    with security.SessionLocal() as db:
        if opp_id.isdigit():
            opp = db.get(Opportunity, int(opp_id))
        if opp is not None and opp.lead_key:
            have = {(u or "").rstrip("/").lower() for u in db.scalars(
                select(Contact.linkedin_url).where(Contact.lead_key == opp.lead_key, Contact.linkedin_url.is_not(None)))}
    return _render(request, "employees.html", {
        "active": "employees", "company": company, "opp": opp, "items": employees.items_of(search),
        "search": search, "from_cache": from_cache, "limit": limit, "max": employees.HARD_MAX,
        "ready": enabled(), "have": have, "page_error": page_error,
    })


@router.get("/tools/employees")
def employees_page(request: Request, company: str = "", opp: str = "", me: User = Depends(REQUIRE["sales"])):
    company = company.strip()[:200]
    search = None
    if company:
        with security.SessionLocal() as db:
            search = employees.cached(db, company)  # əvvəlki (pulsuz) nəticə dərhal göstərilir
    return _page(request, company=company, opp_id=opp, search=search, from_cache=search is not None)


@router.post("/tools/employees", dependencies=[Depends(verify_csrf)])
def employees_search(request: Request, company: str = Form(""), limit: int = Form(employees.DEFAULT_MAX),
                     force: str = Form(""), opp: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    company = " ".join(company.split())[:200]
    if len(company) < 2:
        return _page(request, company=company, opp_id=opp, page_error="Şirkətin adını yaz")
    with security.SessionLocal() as db:
        try:
            search, from_cache = employees.find(db, company, limit, force=force == "1", user_id=me.id)
        except LeadersError as exc:
            return _page(request, company=company, opp_id=opp, limit=limit, page_error=str(exc),
                         search=employees.cached(db, company))
    return _page(request, company=company, opp_id=opp, search=search, from_cache=from_cache, limit=limit)


@router.post("/tools/employees/contact", dependencies=[Depends(verify_csrf)])
def employees_add_contact(opp: int = Form(...), company: str = Form(""), name: str = Form(""), title: str = Form(""),
                          linkedin_url: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    """İşçini fürsətin kontaktlarına əlavə edir (sonra məktub yazmaq olar)."""
    back = f"/tools/employees?company={quote(company[:200])}&opp={opp}"
    url = linkedin_url.strip()
    if not url.startswith("https://") or "linkedin.com/" not in url:
        return _go(back, err="LinkedIn linki yanlışdır")
    with security.SessionLocal() as db:
        row = db.get(Opportunity, opp)
        if row is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        if row.lead_key and db.scalar(select(Contact.id).where(Contact.lead_key == row.lead_key,
                                                               Contact.linkedin_url == url)):
            return _go(back, msg=f"{name} artıq kontaktlardadır")
        db.add(Contact(organization_id=row.organization_id, lead_key=row.lead_key, name=name.strip()[:200] or None,
                       title=title.strip()[:200] or None, linkedin_url=url, source="enrichment"))
        db.commit()
    return _go(back, msg=f"{name} fürsətin kontaktlarına əlavə olundu")
