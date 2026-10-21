"""Alətlər → Şirkət əlavə et: müştəri şirkətlər və müqavilələrinin bitmə tarixi.

Müqaviləsi bitməyə yaxın olanlar (defolt 90 gün) yuxarıda görünür; hər istifadəçi həmin
səhifədən özünə xatırlatma qurur (app/notify.py — "X gün/saat əvvəl" saytda bildiriş).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import delete, func, select

from app import notify as nt
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm_routes import BAKU_OFFSET, _go, _render
from app.db.models import ClientContract, Reminder, User
from app.tenders import organization_for

router = APIRouter()

SOON_DAYS = 90
REMIND_HOUR = 9  # xatırlatma defolt olaraq bitmə günü saat 09:00 (Bakı) üçün qurulur


def _today() -> date:
    return (datetime.utcnow() + BAKU_OFFSET).date()


@router.get("/tools/contracts")
def contracts_page(request: Request, show: str = "soon", me: User = Depends(REQUIRE["sales"])):
    today = _today()
    with security.SessionLocal() as db:
        nt.fire_due(db, me.id)
        q = select(ClientContract).order_by(ClientContract.contract_end)
        if show != "all":
            q = q.where(ClientContract.contract_end <= today + timedelta(days=SOON_DAYS))
        rows = list(db.scalars(q))
        total = db.scalar(select(func.count()).select_from(ClientContract)) or 0
        mine: dict[int, list[Reminder]] = {}
        if rows:
            for r in db.scalars(select(Reminder).where(Reminder.user_id == me.id, Reminder.done_at.is_(None),
                                                       Reminder.contract_id.in_([c.id for c in rows]))
                                .order_by(Reminder.remind_at)):
                mine.setdefault(r.contract_id, []).append(r)
        authors = {u.id: u.full_name for u in db.scalars(select(User))}
    return _render(request, "contracts.html", {
        "active": "contracts", "rows": rows, "show": show, "today": today, "soon_days": SOON_DAYS, "total": total,
        "mine": mine, "authors": authors, "units": nt.UNIT_NAMES, "describe": nt.describe_before,
        "remind_hour": REMIND_HOUR,
    })


@router.post("/tools/contracts", dependencies=[Depends(verify_csrf)])
def contracts_add(company_name: str = Form(""), contract_end: str = Form(""), contact: str = Form(""),
                  note: str = Form(""), remind: str = Form(""), before: str = Form("30"), unit: str = Form("day"),
                  me: User = Depends(REQUIRE["sales"])):
    name = " ".join(company_name.split())[:300]
    if len(name) < 2:
        return _go("/tools/contracts", err="Şirkətin adını yaz")
    try:
        end = date.fromisoformat(contract_end)
    except ValueError:
        return _go("/tools/contracts", err="Müqavilənin bitmə tarixini seç")
    with security.SessionLocal() as db:
        org = organization_for(db, name, None)
        row = ClientContract(company_name=name, organization_id=org.id if org else None, contract_end=end,
                             contact=contact.strip()[:300] or None, note=note.strip()[:2000] or None,
                             created_by_id=me.id, created_at=datetime.utcnow())
        db.add(row)
        db.commit()
        msg = f"«{name}» əlavə olundu"
        if remind == "1":
            try:
                minutes = nt.before_minutes(before or "0", unit)
                due = datetime(end.year, end.month, end.day, REMIND_HOUR) - BAKU_OFFSET
                if due - timedelta(minutes=minutes) < datetime.utcnow():
                    raise nt.ReminderError("xatırlatma vaxtı keçmişdə qalır")
                nt.add_reminder(db, me.id, f"Müqavilə bitir: {name}", due, minutes, contract_id=row.id)
                msg += f"; xatırlatma: {nt.describe_before(minutes)}"
            except nt.ReminderError as exc:
                return _go(f"/tools/contracts?show=all#c{row.id}", err=f"{msg}, amma xatırlatma qurulmadı: {exc}")
    return _go(f"/tools/contracts?show=all#c{row.id}", msg=msg)


@router.post("/tools/contracts/{cid}/delete", dependencies=[Depends(verify_csrf)])
def contracts_delete(cid: int, me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        row = db.get(ClientContract, cid)
        if row is None:
            return _go("/tools/contracts", err="Şirkət tapılmadı")
        if row.created_by_id != me.id and not security.has_role(me, "manager"):
            return _go("/tools/contracts", err="Başqasının əlavə etdiyi şirkəti yalnız menecer silə bilər")
        db.execute(delete(Reminder).where(Reminder.contract_id == cid))
        db.delete(row)
        db.commit()
    return _go("/tools/contracts", msg="Silindi")
