"""Hər istifadəçinin öz alətləri: Validasiya süzgəci və kateqoriyaları, məktub şablonları,
xatırlatmalar (qeydlər) və saytdaxili bildirişlər."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta
from urllib.parse import quote
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select

from app import lead_filters as lf
from app import notify as nt
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.crm_routes import BAKU_OFFSET, _parse_local_datetime
from app.crm import CrmError
from app.db.models import LetterTemplate, Notification, Reminder, User, UserCategory

router = APIRouter()

KIND_LABELS = {"tender": "Tender", "employer": "İşəgötürən", "news": "Xəbər"}


def _render(request: Request, name: str, context: dict):
    return request.app.state.templates.TemplateResponse(request, name, {"request": request, **context})


def _go(path: str, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    anchor = ""
    if "#" in path:
        path, anchor = path.split("#", 1)
        anchor = "#" + anchor
    sep = "&" if "?" in path else "?"
    if msg:
        path += f"{sep}msg={quote(msg)}"
    elif err:
        path += f"{sep}err={quote(err)}"
    return RedirectResponse(url=path + anchor, status_code=303)


# --- 1. Validasiya: şəxsi A/B/C süzgəci və öz kateqoriyalarım ------------------------------


@router.get("/me/categories")
def categories_page(request: Request, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        cats = list(db.scalars(select(UserCategory).where(UserCategory.user_id == me.id).order_by(UserCategory.name)))
        user = db.get(User, me.id)
        filters = lf.parse_filters(user.lead_filters if user else None)
    return _render(request, "my_categories.html", {
        "active": "my_categories", "cats": cats, "filters": filters, "tiers": lf.TIERS, "kinds": KIND_LABELS,
        "limit": lf.MAX_CATEGORIES, "lf": lf,
    })


@router.post("/me/categories/filters", dependencies=[Depends(verify_csrf)])
async def categories_filters(request: Request, me: User = Depends(REQUIRE["viewer"])):
    form = await request.form()
    wanted = {t: [k for k in lf.KINDS if form.get(f"{t}_{k}")] for t in lf.TIERS}
    if any(not kinds for kinds in wanted.values()):
        return _go("/me/categories", err="Hər kateqoriyada (A, B, C) ən azı bir lead növü seçilməlidir")
    with security.SessionLocal() as db:
        user = db.get(User, me.id)
        user.lead_filters = lf.dump_filters(wanted)
        db.commit()
    return _go("/me/categories", msg="Süzgəc yadda saxlandı — Validasiyada yalnız seçdiyin növlər görünəcək")


@router.post("/me/categories", dependencies=[Depends(verify_csrf)])
async def categories_add(request: Request, me: User = Depends(REQUIRE["viewer"])):
    form = await request.form()
    name = " ".join(str(form.get("name", "")).split())[:60]
    tiers = "".join(t for t in lf.TIERS if form.get(f"tier_{t}"))
    kinds = ",".join(k for k in lf.KINDS if form.get(f"kind_{k}"))
    terms = "\n".join(dict.fromkeys(t.strip()[:80] for t in re.split(r"[\n,]", str(form.get("terms", ""))) if t.strip()))
    if not name:
        return _go("/me/categories", err="Kateqoriyanın adını yaz")
    if not (tiers or kinds or terms):
        return _go("/me/categories", err="Ən azı bir şərt seç: A/B/C, lead növü və ya açar söz")
    with security.SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(UserCategory).where(UserCategory.user_id == me.id))
        if count >= lf.MAX_CATEGORIES:
            return _go("/me/categories", err=f"Ən çox {lf.MAX_CATEGORIES} kateqoriya ola bilər")
        db.add(UserCategory(user_id=me.id, name=name, tiers=tiers or None, kinds=kinds or None,
                            terms=terms[:4000] or None, created_at=datetime.utcnow()))
        db.commit()
    return _go("/me/categories", msg=f"«{name}» kateqoriyası yaradıldı — Validasiyada ayrıca tab kimi görünür")


@router.post("/me/categories/{cat_id}/delete", dependencies=[Depends(verify_csrf)])
def categories_delete(cat_id: int, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        cat = db.get(UserCategory, cat_id)
        if cat is None or cat.user_id != me.id:
            return _go("/me/categories", err="Kateqoriya tapılmadı")
        name = cat.name
        db.delete(cat)
        db.commit()
    return _go("/me/categories", msg=f"«{name}» silindi")


# --- 4. Məktub şablonları ----------------------------------------------------------------

MAX_TEMPLATE_BYTES = 1024 * 1024
MAX_TEMPLATE_CHARS = 8000
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(data: bytes) -> str:
    """.docx → mətn (paraqraflar sətirlə) — əlavə paket olmadan, zip içindəki document.xml-dən."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ValueError("Word faylı oxunmadı — .docx olduğuna əmin ol və ya mətni yapışdır")
    lines = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def template_text(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if len(data) > MAX_TEMPLATE_BYTES:
        raise ValueError("Fayl 1 MB-dan böyük ola bilməz")
    if ext == "docx":
        text = _docx_text(data)
    elif ext in ("txt", "md", "text", ""):
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("Faylın kodlaşdırması tanınmadı — UTF-8 mətn faylı yüklə")
    else:
        raise ValueError("Yalnız .docx, .txt və ya .md faylı yükləmək olar (və ya mətni yapışdır)")
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()


@router.get("/me/templates")
def templates_page(request: Request, me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        rows = db.execute(select(LetterTemplate, User.full_name).join(User, User.id == LetterTemplate.user_id).where(
            (LetterTemplate.user_id == me.id) | LetterTemplate.is_shared.is_(True)).order_by(LetterTemplate.name)).all()
    return _render(request, "my_templates.html", {"active": "my_templates", "rows": rows, "me": me,
                                                   "max_chars": MAX_TEMPLATE_CHARS})


@router.post("/me/templates", dependencies=[Depends(verify_csrf)])
def templates_add(name: str = Form(""), body: str = Form(""), shared: str = Form(""),
                  file: UploadFile | None = File(default=None), me: User = Depends(REQUIRE["sales"])):
    name = " ".join(name.split())[:120]
    text = body.strip()
    if file is not None and file.filename:
        try:
            text = template_text(file.filename, file.file.read(MAX_TEMPLATE_BYTES + 1))
        except ValueError as exc:
            return _go("/me/templates", err=str(exc))
        name = name or file.filename.rsplit(".", 1)[0][:120]
    if not name:
        return _go("/me/templates", err="Şablonun adını yaz")
    if len(text) < 30:
        return _go("/me/templates", err="Şablon mətni çox qısadır — faylı yüklə və ya mətni yapışdır")
    if len(text) > MAX_TEMPLATE_CHARS:
        return _go("/me/templates", err=f"Şablon çox uzundur (ən çox {MAX_TEMPLATE_CHARS} simvol)")
    with security.SessionLocal() as db:
        db.add(LetterTemplate(user_id=me.id, name=name, body=text, is_shared=shared == "1",
                              created_at=datetime.utcnow()))
        db.commit()
    return _go("/me/templates", msg=f"«{name}» şablonu yükləndi — məktub yazanda seç")


@router.post("/me/templates/{tpl_id}/delete", dependencies=[Depends(verify_csrf)])
def templates_delete(tpl_id: int, me: User = Depends(REQUIRE["sales"])):
    with security.SessionLocal() as db:
        tpl = db.get(LetterTemplate, tpl_id)
        if tpl is None or (tpl.user_id != me.id and not security.has_role(me, "admin")):
            return _go("/me/templates", err="Şablon tapılmadı və ya sənin deyil")
        name = tpl.name
        db.delete(tpl)
        db.commit()
    return _go("/me/templates", msg=f"«{name}» silindi")


# --- 10. Xatırlatmalar ---------------------------------------------------------------------


@router.get("/me/reminders")
def reminders_page(request: Request, show: str = "open", me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        nt.fire_due(db, me.id)
        q = select(Reminder).where(Reminder.user_id == me.id)
        q = q.where(Reminder.done_at.is_(None)) if show != "done" else q.where(Reminder.done_at.is_not(None))
        rows = list(db.scalars(q.order_by(Reminder.due_at)))
    return _render(request, "my_reminders.html", {
        "active": "my_reminders", "rows": rows, "show": show, "units": nt.UNIT_NAMES, "describe": nt.describe_before,
    })


@router.post("/me/reminders", dependencies=[Depends(verify_csrf)])
def reminders_add(title: str = Form(""), note: str = Form(""), due_at: str = Form(""), before: str = Form("0"),
                  unit: str = Form("min"), back: str = Form("/me/reminders"), contract_id: str = Form(""),
                  me: User = Depends(REQUIRE["viewer"])):
    back = back if back in ("/me/reminders", "/tools/contracts") else "/me/reminders"
    try:
        due = _parse_local_datetime(due_at)
        if due is None:
            raise CrmError("Tarix və saatı seç")
        minutes = nt.before_minutes(before or "0", unit)
    except (CrmError, nt.ReminderError) as exc:
        return _go(back, err=str(exc))
    if due - timedelta(minutes=minutes) < datetime.utcnow() - timedelta(minutes=1):
        return _go(back, err="Xatırlatma vaxtı keçmişdə qalır — tarixi və ya “neçə əvvəl”i dəyiş")
    with security.SessionLocal() as db:
        try:
            nt.add_reminder(db, me.id, title, due, minutes, note,
                            contract_id=int(contract_id) if contract_id.isdigit() else None)
        except nt.ReminderError as exc:
            return _go(back, err=str(exc))
    return _go(back, msg=f"Xatırlatma quruldu: {nt.describe_before(minutes)} bildiriş gələcək")


@router.post("/me/reminders/{rid}/done", dependencies=[Depends(verify_csrf)])
def reminders_done(rid: int, back: str = Form("/me/reminders"), me: User = Depends(REQUIRE["viewer"])):
    back = back if back in ("/me/reminders", "/tools/contracts") else "/me/reminders"
    with security.SessionLocal() as db:
        row = db.get(Reminder, rid)
        if row is None or row.user_id != me.id:
            return _go(back, err="Xatırlatma tapılmadı")
        row.done_at = None if row.done_at else datetime.utcnow()
        db.commit()
    return _go(back, msg="Yadda saxlandı")


@router.post("/me/reminders/{rid}/delete", dependencies=[Depends(verify_csrf)])
def reminders_delete(rid: int, back: str = Form("/me/reminders"), me: User = Depends(REQUIRE["viewer"])):
    back = back if back in ("/me/reminders", "/tools/contracts") else "/me/reminders"
    with security.SessionLocal() as db:
        row = db.get(Reminder, rid)
        if row is None or row.user_id != me.id:
            return _go(back, err="Xatırlatma tapılmadı")
        db.delete(row)
        db.commit()
    return _go(back, msg="Xatırlatma silindi")


# --- Bildirişlər ---------------------------------------------------------------------------


@router.get("/notifications")
def notifications_page(request: Request, me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        nt.fire_due(db, me.id)
        rows = list(db.scalars(select(Notification).where(Notification.user_id == me.id)
                               .order_by(Notification.id.desc()).limit(100)))
    return _render(request, "notifications.html", {"active": "notifications", "rows": rows})


@router.get("/api/notifications")
def notifications_api(after: int = 0, me: User = Depends(REQUIRE["viewer"])):
    """Brauzer hər dəqiqə çağırır: vaxtı çatan xatırlatmalar burada bildirişə çevrilir."""
    with security.SessionLocal() as db:
        nt.fire_due(db, me.id)
        rows = nt.unread(db, me.id, after_id=after)
        count = nt.unread_count(db, me.id)
    return JSONResponse({"unread": count, "items": [
        {"id": n.id, "text": n.text, "link": n.link, "at": (n.created_at + BAKU_OFFSET).strftime("%d.%m %H:%M")}
        for n in rows]})


def _safe_link(link: str | None) -> str:
    return link if link and link.startswith("/") and not link.startswith("//") and "\\" not in link else "/notifications"


@router.post("/notifications/{nid}/read", dependencies=[Depends(verify_csrf)])
def notifications_read(nid: int, me: User = Depends(REQUIRE["viewer"])):
    """Bildirişə klik: oxunmuş olur və linkinə keçir."""
    with security.SessionLocal() as db:
        row = db.get(Notification, nid)
        if row is None or row.user_id != me.id:
            return _go("/notifications", err="Bildiriş tapılmadı")
        nt.mark_read(db, me.id, nid)
        link = row.link
    return RedirectResponse(url=_safe_link(link), status_code=303)


@router.post("/notifications/read-all", dependencies=[Depends(verify_csrf)])
def notifications_read_all(me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        nt.mark_read(db, me.id)
    return _go("/notifications", msg="Hamısı oxunmuş kimi qeyd edildi")
