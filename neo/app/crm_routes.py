"""Fürsətlər və tapşırıqlar ekranları (öz CRM-imiz, bax app/crm.py).

Rollar: viewer baxır; sales mərhələni dəyişir, tapşırıq əlavə edir/bağlayır;
manager+ məsul şəxsi dəyişir.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app import crm
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.db.models import LeadRecord, Opportunity, Organization, Task, Tender, User

router = APIRouter()

BAKU_OFFSET = timedelta(hours=4)


def _render(request: Request, name: str, context: dict):
    return request.app.state.templates.TemplateResponse(request, name, {"request": request, **context})


def _go(path: str, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    if msg:
        path += f"{sep}msg={quote(msg)}"
    elif err:
        path += f"{sep}err={quote(err)}"
    return RedirectResponse(url=path, status_code=303)


def _parse_local_datetime(value: str | None) -> datetime | None:
    """<input type="datetime-local"> Bakı vaxtıdır → naive UTC (bazadakı format)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value) - BAKU_OFFSET
    except ValueError:
        raise crm.CrmError("Tarix formatı yanlışdır")


def _active_users(db) -> list[User]:
    return list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)))


# --- Fürsətlər -------------------------------------------------------------------


CLOSED_SHOWN = 15  # lövhədə qazanılan/uduzulan sütunlarda son N fürsət


@router.get("/opportunities")
def opportunities_page(request: Request, owner: str = "all", me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        next_due = (
            select(Task.opportunity_id, func.min(Task.due_at).label("due"))
            .where(Task.status.in_(("open", "in_progress")))
            .group_by(Task.opportunity_id)
            .subquery()
        )
        q = (
            select(Opportunity, Organization, User, next_due.c.due)
            .outerjoin(Organization, Organization.id == Opportunity.organization_id)
            .outerjoin(User, User.id == Opportunity.owner_id)
            .outerjoin(next_due, next_due.c.opportunity_id == Opportunity.id)
        )
        if owner == "me":
            q = q.where(Opportunity.owner_id == me.id)
        rows = db.execute(q.order_by(Opportunity.updated_at.desc())).all()
    columns = {stage: [] for stage in crm.PIPELINE}
    for row in rows:
        columns[row[0].stage].append(row)
    totals = {stage: len(items) for stage, items in columns.items()}
    for stage in crm.CLOSED_STAGES:
        columns[stage] = columns[stage][:CLOSED_SHOWN]
    return _render(request, "opportunities.html", {
        "active": "opportunities", "columns": columns, "totals": totals, "owner": owner,
        "stage_names": crm.STAGE_NAMES, "open_total": sum(totals[s] for s in crm.PIPELINE if s not in crm.CLOSED_STAGES),
    })


@router.get("/opportunities/{opp_id}")
def opportunity_page(request: Request, opp_id: int, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        lead = db.get(LeadRecord, opp.lead_key) if opp.lead_key else None
        org = db.get(Organization, opp.organization_id) if opp.organization_id else None
        tender = db.get(Tender, opp.tender_id) if opp.tender_id else None
        tasks = crm.opp_tasks(db, opp.id)
        users = _active_users(db)
    return _render(request, "opportunity.html", {
        "active": "opportunities", "opp": opp, "lead": lead, "org": org, "tender": tender,
        "tasks": tasks, "users": {u.id: u for u in users}, "user_list": users,
        "stages": crm.pipeline_for(opp), "stage_names": crm.STAGE_NAMES,
        "lost_reasons": crm.LOST_REASON_NAMES, "task_status_names": crm.TASK_STATUS_NAMES,
        "priority_names": crm.PRIORITY_NAMES,
    })


@router.post("/opportunities/{opp_id}/stage", dependencies=[Depends(verify_csrf)])
def opportunity_stage(opp_id: int, stage: str = Form(...), lost_reason: str = Form(""),
                      lost_note: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        try:
            crm.change_stage(db, opp, stage, lost_reason or None, lost_note)
        except crm.CrmError as exc:
            return _go(path, err=str(exc))
    return _go(path, msg=f"Mərhələ: {crm.STAGE_NAMES[stage]}")


@router.post("/opportunities/{opp_id}/details", dependencies=[Depends(verify_csrf)])
def opportunity_details(opp_id: int, expected_value: str = Form(""), expected_close_on: str = Form(""),
                        owner_id: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        try:
            opp.expected_value = Decimal(expected_value.replace(",", ".")) if expected_value.strip() else None
        except InvalidOperation:
            return _go(path, err="Gözlənilən dəyər rəqəm olmalıdır")
        try:
            opp.expected_close_on = datetime.fromisoformat(expected_close_on).date() if expected_close_on else None
        except ValueError:
            return _go(path, err="Bağlanma tarixi yanlışdır")
        new_owner = int(owner_id) if owner_id.isdigit() else None
        if new_owner != opp.owner_id:
            # Başqasına təhvil vermək menecer işidir; satış yalnız özünə götürə bilər.
            if not security.has_role(me, "manager") and new_owner != me.id:
                return _go(path, err="Məsul şəxsi yalnız menecer dəyişə bilər")
            if new_owner is not None and db.get(User, new_owner) is None:
                return _go(path, err="İstifadəçi tapılmadı")
            opp.owner_id = new_owner
        opp.updated_at = datetime.utcnow()
        db.commit()
    return _go(path, msg="Yadda saxlandı")


@router.post("/opportunities/{opp_id}/tasks", dependencies=[Depends(verify_csrf)])
def opportunity_add_task(opp_id: int, title: str = Form(...), due_at: str = Form(""),
                         assignee_id: str = Form(""), priority: str = Form("normal"),
                         description: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        try:
            crm.add_task(db, opp, title, _parse_local_datetime(due_at),
                         int(assignee_id) if assignee_id.isdigit() else me.id,
                         me.id, priority, description)
        except crm.CrmError as exc:
            return _go(path, err=str(exc))
    return _go(path, msg="Tapşırıq əlavə olundu")


# --- Tapşırıqlar -----------------------------------------------------------------


@router.get("/tasks")
def tasks_page(request: Request, who: str = "me", status: str = "open", me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        q = (
            select(Task, Opportunity, Organization, User)
            .join(Opportunity, Opportunity.id == Task.opportunity_id)
            .outerjoin(Organization, Organization.id == Opportunity.organization_id)
            .outerjoin(User, User.id == Task.assignee_id)
        )
        if who == "me":
            q = q.where(Task.assignee_id == me.id)
        if status == "open":
            q = q.where(Task.status.in_(("open", "in_progress")))
        elif status in crm.TASK_STATUS_NAMES:
            q = q.where(Task.status == status)
        # Son tarixi olanlar əvvəl (ən təcili yuxarıda), sonra tarixsizlər.
        rows = db.execute(q.order_by(Task.due_at.is_(None), Task.due_at, Task.id)).all()
    # "Bu gün" = Bakı vaxtı ilə günün sonuna qədər (bazada naive UTC).
    now_baku = datetime.utcnow() + BAKU_OFFSET
    today_end = now_baku.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - BAKU_OFFSET
    return _render(request, "tasks.html", {
        "active": "tasks", "rows": rows, "who": who, "status": status, "today_end": today_end,
        "task_status_names": crm.TASK_STATUS_NAMES, "priority_names": crm.PRIORITY_NAMES,
    })


@router.post("/tasks/{task_id}/status", dependencies=[Depends(verify_csrf)])
def task_status(task_id: int, status: str = Form(...), back: str = Form("/tasks"),
                me: User = Depends(REQUIRE["sales"])):
    back = back if back.startswith("/") and not back.startswith("//") else "/tasks"
    with security.SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            return _go(back, err="Tapşırıq tapılmadı")
        try:
            crm.set_task_status(db, task, status)
        except crm.CrmError as exc:
            return _go(back, err=str(exc))
    return _go(back, msg=f"Tapşırıq: {crm.TASK_STATUS_NAMES[status]}")
