"""Alətlər → Şirkət analizi (app/company_analysis.py).

Uğurlu analiz şirkət bazasına yazılır (app/companies.py, səhifə — app/company_db_routes.py);
işçilər (app/employees.py) və alt şirkətlər (app/subsidiaries.py) ayrıca JSON sorğuları ilə gəlir.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import companies, company_analysis, employees, subsidiaries
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm import BAKU_OFFSET
from app.db.models import Organization, User
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
                     staff=employees.items_of(staff), staff_at=staff.created_at if staff else None,
                     **(subsidiaries.context(db, saved) if saved else {}))


def _employees_json(request: Request, row, from_cache: bool) -> dict:
    """items — vəzifəyə görə sıralı; html — səhifədəki ilə eyni qruplu siyahı (JS ayrıca qurmur)."""
    items = employees.items_of(row)
    groups = request.app.state.templates.env.get_template("_employees_list.html").module.employee_groups
    return {"items": items, "html": str(groups(items)), "from_cache": from_cache,
            "searched_at": (row.created_at + BAKU_OFFSET).strftime("%d.%m.%Y %H:%M")}


def _company_name(company: str) -> str:
    return " ".join(company.split())[:200]


@router.post("/tools/company/employees", dependencies=[Depends(verify_csrf)])
def company_employees(request: Request, company: str = Form(""), force: str = Form(""),
                      limit: int = Form(employees.DEFAULT_MAX), me: User = Depends(REQUIRE["sales"])):
    """JSON: keşdə təzə nəticə varsa dərhal {items}; yoxdursa (və ya force=1) Apify run başladılır → {run}.
    Run Vercel-in 60 saniyəsindən uzun çəkə bilər — nəticəni panel /poll ilə götürür."""
    company = _company_name(company)
    if len(company) < 2:
        return JSONResponse({"error": "Şirkətin adı yoxdur"}, status_code=400)
    with security.SessionLocal() as db:
        row = None if force == "1" else employees.fresh(db, company)
        if row is not None:
            return _employees_json(request, row, True)
    try:
        return {"run": employees.start(company, limit)}
    except LeadersError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.post("/tools/company/employees/poll", dependencies=[Depends(verify_csrf)])
def company_employees_poll(request: Request, company: str = Form(""), run: str = Form(""),
                           limit: int = Form(employees.DEFAULT_MAX), me: User = Depends(REQUIRE["sales"])):
    """JSON: {pending} — hələ işləyir; {items} — bitdi və saxlanıldı."""
    company = _company_name(company)
    if len(company) < 2:
        return JSONResponse({"error": "Şirkətin adı yoxdur"}, status_code=400)
    with security.SessionLocal() as db:
        try:
            row = employees.poll(db, run, company, limit, user_id=me.id)
        except LeadersError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return {"pending": True} if row is None else _employees_json(request, row, False)


# --- Alt şirkətlər (app/subsidiaries.py) -------------------------------------------------------------------------


def _macro(request: Request, name: str):
    return getattr(request.app.state.templates.env.get_template("_subsidiaries.html").module, name)


@router.post("/tools/company/{org_id}/subsidiaries", dependencies=[Depends(verify_csrf)])
def company_subsidiaries(request: Request, org_id: int, force: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    """JSON {html, count, checked_at}: əvvəl araşdırılıbsa (və force yoxdursa) saxlanmış siyahı, yoxsa AI + veb axtarışı."""
    with security.SessionLocal() as db:
        org = db.get(Organization, org_id)
        if org is None:
            return JSONResponse({"error": "Şirkət tapılmadı"}, status_code=404)
        if force == "1" or org.subsidiaries_checked_at is None:
            try:
                found = subsidiaries.research(org.name, org.website or "")
            except OutreachError as exc:
                return JSONResponse({"error": str(exc)}, status_code=502)
            subsidiaries.save(db, org, found)
        cards = [subsidiaries.card(o) for o in subsidiaries.of(db, org)]
        return {"html": str(_macro(request, "subsidiaries_list")(cards)), "count": len(cards),
                "checked_at": (org.subsidiaries_checked_at + BAKU_OFFSET).strftime("%d.%m.%Y %H:%M")}


@router.post("/tools/company/subsidiary/{org_id}/analyse", dependencies=[Depends(verify_csrf)])
def subsidiary_analyse(request: Request, org_id: int, me: User = Depends(REQUIRE["sales"])):
    """JSON {html}: alt şirkəti analiz edib şirkət bazasına yazır (artıq analiz olunubsa təkrar ödənilmir)."""
    with security.SessionLocal() as db:
        org = db.get(Organization, org_id)
        if org is None or org.parent_id is None:
            return JSONResponse({"error": "Alt şirkət tapılmadı"}, status_code=404)
        if org.analysed_at is None:
            try:
                result = company_analysis.analyse(db, org.name, org.website or "")
            except OutreachError as exc:
                return JSONResponse({"error": str(exc)}, status_code=502)
            try:
                companies.save_analysis(db, result, org.website, me.id)
            except companies.CompanyError as exc:
                db.rollback()
                return JSONResponse({"error": str(exc)}, status_code=422)
            db.refresh(org)
        return {"html": str(_macro(request, "subsidiary_card")(subsidiaries.card(org)))}
