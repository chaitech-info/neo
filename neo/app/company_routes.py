"""Alətlər → Şirkət analizi (app/company_analysis.py).

Uğurlu analiz şirkət bazasına yazılır (app/companies.py, səhifə — app/company_db_routes.py);
işçilər ayrıca JSON sorğusu ilə axtarılır (app/employees.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import companies, company_analysis, employees
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm import BAKU_OFFSET
from app.db.models import User
from app.leaders import LeadersError
from app.leaders import enabled as employees_enabled
from app.outreach import OutreachError

router = APIRouter()


def _page(request: Request, **context):
    return request.app.state.templates.TemplateResponse(request, "company.html", {
        "request": request, "active": "company", "size_names": company_analysis.SIZE_NAMES,
        "fit_names": company_analysis.FIT_NAMES, "employees_ready": employees_enabled(), **context,
    })


@router.get("/tools/company")
def company_page(request: Request, name: str = "", website: str = "", me: User = Depends(REQUIRE["sales"])):
    return _page(request, name=name[:200], website=website[:300], result=None)


@router.post("/tools/company", dependencies=[Depends(verify_csrf)])
def company_analyse(request: Request, name: str = Form(""), website: str = Form(""),
                    me: User = Depends(REQUIRE["sales"])):
    name, website = name.strip()[:200], website.strip()[:300]
    with security.SessionLocal() as db:
        try:
            result = company_analysis.analyse(db, name, website)
        except OutreachError as exc:
            return _page(request, name=name, website=website, result=None, page_error=str(exc))
        try:
            saved = companies.save_analysis(db, result, website, me.id)
        except companies.CompanyError:
            db.rollback()
            saved = None
        staff = employees.cached(db, result["name"])
        users = {u.id: u.full_name for u in db.scalars(select(User))}
        return _page(request, name=name, website=website, result=result, saved=saved, users=users,
                     staff=employees.items_of(staff), staff_at=staff.created_at if staff else None)


@router.post("/tools/company/employees", dependencies=[Depends(verify_csrf)])
def company_employees(company: str = Form(""), force: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    """JSON: şirkətin LinkedIn işçiləri (keşdə varsa Apify-a getmir; force=1 — yenidən axtar)."""
    company = " ".join(company.split())[:200]
    if len(company) < 2:
        return JSONResponse({"error": "Şirkətin adı yoxdur"}, status_code=400)
    with security.SessionLocal() as db:
        try:
            row, from_cache = employees.find(db, company, force=force == "1", user_id=me.id)
        except LeadersError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return {"items": employees.items_of(row), "from_cache": from_cache, "searched_at": (row.created_at + BAKU_OFFSET).strftime("%d.%m.%Y %H:%M")}
