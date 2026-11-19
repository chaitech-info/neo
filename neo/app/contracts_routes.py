"""Alətlər → Şirkət əlavə et (/tools/contracts) və Satış → Müqavilələr (/contracts).

"Şirkət əlavə et": şirkət analizinin göstərdiyi məlumatı (qısa məlumat, sahə, ölçü, catering uyğunluğu, əlaqə,
rəhbərlər, xəbərlər, mənbələr) istifadəçi özü yazır — AI-sız. Şirkət şirkət bazasına düşür (app/companies.py,
dublikatsız); `?org=<id>` ilə eyni forma mövcud şirkətin məlumatını dəyişir. Müqavilə bu səhifədə yoxdur:
əməkdaşlıq razılaşmaları şirkət kartında (/companies/{id}#agreements) əlavə olunur, hamısı "Müqavilələr"dədir.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import and_, delete, func, or_, select

from app import companies, company_analysis
from app import notify as nt
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm_routes import BAKU_OFFSET, _go, _render
from app.db.models import ClientContract, Organization, Reminder, User

router = APIRouter()

SOON_DAYS = 90
REMIND_HOUR = 9  # xatırlatma defolt olaraq bitmə günü saat 09:00 (Bakı) üçün qurulur


def _today() -> date:
    return (datetime.utcnow() + BAKU_OFFSET).date()


def _company_form(request: Request, form: dict, org: Organization | None = None, error: str | None = None):
    return _render(request, "contracts.html", {
        "active": "contracts", "form": form, "org": org, "page_error": error,
        "size_names": {k: company_analysis.SIZE_NAMES[k] for k in companies.MANUAL_SIZES},
        "fit_names": {k: company_analysis.FIT_NAMES[k] for k in companies.MANUAL_FITS},
    })


@router.get("/tools/contracts")
def contracts_page(request: Request, org: str = "", me: User = Depends(REQUIRE["sales"])):
    """Boş forma; `?org=<id>` — mövcud şirkətin əl ilə daxil edilmiş məlumatı ilə doldurulmuş."""
    with security.SessionLocal() as db:
        found = db.get(Organization, int(org)) if org.isdigit() else None
        if org and found is None:
            return _go("/companies", err="Şirkət tapılmadı")
        form = companies.manual_form(companies.manual_of(found) if found else None)
        if found is not None:
            form.update(company_name=found.name, website=found.website or "", voen=found.voen or "")
            for key in ("phone", "email", "industry"):   # kartda yazılmış əlaqə formada da görünsün
                form[key] = form[key] or getattr(found, key) or ""
        return _company_form(request, form, found)


REGISTER_FILTERS = {"all": "Hamısı", "active": "Qüvvədə", "soon": f"Bitməyə yaxın ({SOON_DAYS} gün)",
                    "expired": "Bitib", "draft": "Layihə", "terminated": "Ləğv edilib"}


def _register_where(key: str, today: date):
    """"Müqavilələr" süzgəci; "Qüvvədə"/"Bitib" görünən statusa uyğundur (companies.shown_status)."""
    c = ClientContract
    live = or_(c.status == "active", c.status.is_(None))
    return {
        "active": and_(live, or_(c.contract_end.is_(None), c.contract_end >= today)),
        "soon": and_(c.status.in_(("active", "draft")), c.contract_end <= today + timedelta(days=SOON_DAYS)),
        "expired": or_(c.status == "expired", and_(live, c.contract_end < today)),
        "draft": c.status == "draft",
        "terminated": c.status == "terminated",
    }.get(key)


@router.get("/contracts")
def contracts_register(request: Request, show: str = "all", q: str = "", me: User = Depends(REQUIRE["sales"])):
    """Satış → Müqavilələr: bütün əməkdaşlıq razılaşmalarının reyestri (süzgəc + axtarış)."""
    show = show if show in REGISTER_FILTERS else "all"
    q = q.strip()[:200]
    today = _today()
    with security.SessionLocal() as db:
        nt.fire_due(db, me.id)
        search = None
        if q:
            like = f"%{q}%"
            search = or_(ClientContract.company_name.ilike(like), ClientContract.title.ilike(like),
                         ClientContract.contact.ilike(like))
        counts = {}
        for key in REGISTER_FILTERS:
            cq = select(func.count()).select_from(ClientContract)
            for cond in (_register_where(key, today), search):
                if cond is not None:
                    cq = cq.where(cond)
            counts[key] = db.scalar(cq) or 0
        rq = select(ClientContract).order_by(ClientContract.contract_end.is_(None), ClientContract.contract_end,
                                             ClientContract.id.desc())
        for cond in (_register_where(show, today), search):
            if cond is not None:
                rq = rq.where(cond)
        rows = list(db.scalars(rq))
        mine: dict[int, list[Reminder]] = {}
        if rows:
            for r in db.scalars(select(Reminder).where(Reminder.user_id == me.id, Reminder.done_at.is_(None),
                                                       Reminder.contract_id.in_([c.id for c in rows]))
                                .order_by(Reminder.remind_at)):
                mine.setdefault(r.contract_id, []).append(r)
        authors = {u.id: u.full_name for u in db.scalars(select(User))}
    back = "/contracts?" + urlencode({k: v for k, v in (("show", show), ("q", q)) if v})
    return _render(request, "contracts_register.html", {
        "active": "contracts_register", "rows": rows, "show": show, "q": q, "filters": REGISTER_FILTERS,
        "counts": counts, "today": today, "mine": mine, "authors": authors, "units": nt.UNIT_NAMES,
        "describe": nt.describe_before, "remind_hour": REMIND_HOUR, "back": back,
        "status_names": companies.AGREEMENT_STATUS_NAMES, "shown_status": companies.shown_status,
    })


@router.post("/tools/contracts", dependencies=[Depends(verify_csrf)])
def contracts_add(request: Request, org_id: str = Form(""), company_name: str = Form(""), website: str = Form(""),
                  voen: str = Form(""), notes: str = Form(""), summary: str = Form(""), industry: str = Form(""),
                  size: str = Form(""), size_note: str = Form(""), employees: str = Form(""),
                  fit: str = Form(""), fit_reason: str = Form(""), founded: str = Form(""), phone: str = Form(""),
                  email: str = Form(""), address: str = Form(""), locations: str = Form(""),
                  key_people: str = Form(""), news: str = Form(""), sources: str = Form(""),
                  me: User = Depends(REQUIRE["sales"])):
    """Şirkəti və əl ilə yazılmış məlumatını saxla. Səhv olanda forma yazılanlarla birlikdə geri qayıdır."""
    raw = {"summary": summary, "industry": industry, "size": size, "size_note": size_note, "employees": employees,
           "fit": fit, "fit_reason": fit_reason, "founded": founded, "phone": phone, "email": email,
           "address": address, "locations": locations, "key_people": key_people, "news": news, "sources": sources}
    name = " ".join(company_name.split())[:300]
    form = {**raw, "company_name": name, "website": website.strip(), "voen": voen.strip(), "notes": notes}
    with security.SessionLocal() as db:
        editing = db.get(Organization, int(org_id)) if org_id.isdigit() else None
        if org_id and editing is None:
            return _go("/companies", err="Şirkət tapılmadı")

        def fail(message: str):
            db.rollback()
            return _company_form(request, form, editing, message)

        if editing is None and len(name) < 2:
            return fail("Şirkətin adını yaz")
        try:
            fields = companies.parse_manual(raw)
            # Dəyişərkən ad sabitdir (şirkət artıq tapılıb); sayt və VÖEN yalnız boşdursa doldurulur.
            org = companies.company_for(db, editing.name if editing else name, website, voen)
            if editing is not None and org.id != editing.id:
                return fail("Bu sayt və ya VÖEN bazada başqa şirkətdədir")
            if editing is None and notes.strip():  # mövcud şirkətin qeydi itməsin — yeni qeyd sonuna əlavə olunur
                companies.save_notes(org, "\n\n".join(x for x in (org.notes, notes.strip()) if x), me.id)
        except companies.CompanyError as exc:
            return fail(str(exc))
        if editing is None and companies.manual_of(org):
            # Şirkət bazada var idi: yeni yazılanlar əvvəlkinin üstünə gəlir, toxunulmayan sahələr qalır.
            fields = {**{k: v for k, v in companies.manual_of(org).items() if k not in ("by", "at")}, **fields}
        companies.save_manual(org, fields, me.id)
        db.commit()
        return _go(f"/companies/{org.id}#manual", msg=f"«{org.name}» şirkət bazasında — məlumat yadda saxlandı")


@router.post("/tools/contracts/{cid}/delete", dependencies=[Depends(verify_csrf)])
def contracts_delete(cid: int, me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        row = db.get(ClientContract, cid)
        if row is None:
            return _go("/contracts", err="Müqavilə tapılmadı")
        if row.created_by_id != me.id and not security.has_role(me, "manager"):
            return _go("/contracts", err="Başqasının əlavə etdiyi müqaviləni yalnız menecer silə bilər")
        db.execute(delete(Reminder).where(Reminder.contract_id == cid))
        db.delete(row)
        db.commit()
    return _go("/contracts", msg="Silindi")
