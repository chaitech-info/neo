"""Alətlər → Şirkət analizi (app/company_analysis.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app import company_analysis
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.db.models import User
from app.outreach import OutreachError

router = APIRouter()


def _page(request: Request, **context):
    return request.app.state.templates.TemplateResponse(request, "company.html", {
        "request": request, "active": "company", "size_names": company_analysis.SIZE_NAMES,
        "fit_names": company_analysis.FIT_NAMES, **context,
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
    return _page(request, name=name, website=website, result=result)
