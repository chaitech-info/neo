"""Alətlər → Şirkət əlavə et: müştəri şirkət + onunla əməkdaşlıq razılaşmaları (bir neçə ola bilər).

Şirkət şirkət bazasına düşür (app/companies.py, dublikatsız); razılaşmalar sonra şirkət kartında
(/companies/{id}) dəyişdirilir. Müddəti bitməyə yaxın olanlar (defolt 90 gün) burada görünür; hər istifadəçi
özünə xatırlatma qurur (app/notify.py — "X gün/saat əvvəl" saytda bildiriş).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import delete, func, select

from app import companies
from app import notify as nt
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm_routes import BAKU_OFFSET, _go, _render
from app.db.models import ClientContract, Reminder, User

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
        q = select(ClientContract).order_by(ClientContract.contract_end.is_(None), ClientContract.contract_end,
                                            ClientContract.id.desc())
        if show != "all":  # bitməyə yaxın: qüvvədə olan / layihə, bitmə tarixi 90 gün içində
            q = q.where(ClientContract.contract_end <= today + timedelta(days=SOON_DAYS),
                        ClientContract.status.in_(("active", "draft")))
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
        "remind_hour": REMIND_HOUR, "status_names": companies.AGREEMENT_STATUS_NAMES,
        "shown_status": companies.shown_status,
    })


def _agreements_from_form(title: list[str], description: list[str], starts_on: list[str], ends_on: list[str],
                          status: list[str], contact: list[str]) -> list[dict]:
    """Formdakı razılaşma blokları (eyni adlı sahələr siyahı kimi gəlir); tam boş blok atlanır.
    Bir blok səhvdirsə — CompanyError, heç nə yazılmır."""
    out = []
    for i in range(len(title)):
        get = lambda values: values[i] if i < len(values) else ""  # noqa: E731
        if not any(get(v).strip() for v in (title, description, starts_on, ends_on, contact)):
            continue
        try:
            out.append(companies.parse_agreement(get(title), get(description), get(starts_on), get(ends_on),
                                                 get(status) or "active", get(contact)))
        except companies.CompanyError as exc:
            raise companies.CompanyError(f"Razılaşma {i + 1}: {exc}") from exc
    return out


@router.post("/tools/contracts", dependencies=[Depends(verify_csrf)])
def contracts_add(company_name: str = Form(""), website: str = Form(""), voen: str = Form(""), notes: str = Form(""),
                  title: list[str] = Form([]), description: list[str] = Form([]), starts_on: list[str] = Form([]),
                  ends_on: list[str] = Form([]), status: list[str] = Form([]), contact: list[str] = Form([]),
                  remind: str = Form(""), before: str = Form("30"), unit: str = Form("day"),
                  me: User = Depends(REQUIRE["sales"])):
    name = " ".join(company_name.split())[:300]
    if len(name) < 2:
        return _go("/tools/contracts", err="Şirkətin adını yaz")
    try:
        items = _agreements_from_form(title, description, starts_on, ends_on, status, contact)
    except companies.CompanyError as exc:
        return _go("/tools/contracts", err=str(exc))
    with security.SessionLocal() as db:
        try:
            org = companies.company_for(db, name, website, voen)
        except companies.CompanyError as exc:
            return _go("/tools/contracts", err=str(exc))
        companies.list_company(org)
        if notes.strip():  # mövcud şirkətin qeydi itməsin — yeni qeyd sonuna əlavə olunur
            try:
                companies.save_notes(org, "\n\n".join(x for x in (org.notes, notes.strip()) if x), me.id)
            except companies.CompanyError as exc:
                db.rollback()
                return _go("/tools/contracts", err=str(exc))
        rows = [companies.add_agreement(db, org, fields, me.id) for fields in items]
        db.commit()
        msg = f"«{org.name}» şirkət bazasında" + (f", {len(rows)} razılaşma əlavə olundu" if rows else "")
        if remind == "1" and any(r.contract_end for r in rows):
            try:
                minutes = nt.before_minutes(before or "0", unit)
                for row in rows:
                    if row.contract_end is None:
                        continue
                    end = row.contract_end
                    due = datetime(end.year, end.month, end.day, REMIND_HOUR) - BAKU_OFFSET
                    if due - timedelta(minutes=minutes) < datetime.utcnow():
                        raise nt.ReminderError(f"«{row.title}» üçün xatırlatma vaxtı keçmişdə qalır")
                    nt.add_reminder(db, me.id, f"Müqavilə bitir: {org.name} — {row.title}", due, minutes,
                                    contract_id=row.id)
                msg += f"; xatırlatma: {nt.describe_before(minutes)}"
            except nt.ReminderError as exc:
                return _go(f"/companies/{org.id}#agreements", err=f"{msg}, amma xatırlatma qurulmadı: {exc}")
    return _go(f"/companies/{org.id}#agreements", msg=msg)


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
