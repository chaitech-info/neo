"""Fürsətlər və tapşırıqlar ekranları (öz CRM-imiz, bax app/crm.py).

Rollar: viewer baxır; sales mərhələni dəyişir, tapşırıq əlavə edir/bağlayır;
manager+ məsul şəxsi dəyişir.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import crm, leaders, mailboxes
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.contacts import EMAIL_RE, contacts_for, normalize_phone, opportunity_emails
from app.notify import notify
from app.outreach import LANGUAGES, OutreachError, detect_language
from app.outreach import linkedin_comment, mail
from app.db.models import (REPLY_STATUSES, Contact, InboundEmail, LeadRecord, LetterApproval, LetterTemplate,
                           Opportunity, Organization, Outreach, Task, Tender, User)

router = APIRouter()

BAKU_OFFSET = crm.BAKU_OFFSET


def _render(request: Request, name: str, context: dict):
    return request.app.state.templates.TemplateResponse(request, name, {"request": request, **context})


def _go(path: str, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    path, hash_, fragment = path.partition("#")  # ?msg= fraqmentdən (#...) əvvəl olmalıdır, yoxsa brauzer görmür
    sep = "&" if "?" in path else "?"
    if msg:
        path += f"{sep}msg={quote(msg)}"
    elif err:
        path += f"{sep}err={quote(err)}"
    return RedirectResponse(url=path + hash_ + fragment, status_code=303)


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
MAX_EXPECTED_VALUE = Decimal("1000000000000")  # Numeric(14, 2) → ən çox 12 tam rəqəm


@router.get("/opportunities")
def opportunities_page(request: Request, owner: str = "all", reply: str = "", me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        next_due = (
            select(Task.opportunity_id, func.min(Task.due_on).label("due"))
            .where(Task.status.in_(("open", "in_progress")))
            .group_by(Task.opportunity_id)
            .subquery()
        )
        # Kartda bir email: təşkilatın, elandakı, yoxdursa ilk kontaktın (heç biri yoxdursa göstərilmir).
        contact_email = (select(Contact.email).where(Contact.lead_key == Opportunity.lead_key, Contact.email.is_not(None))
                         .order_by(Contact.id).limit(1).correlate(Opportunity).scalar_subquery())
        email = func.coalesce(Organization.email, LeadRecord.contact_email, contact_email)
        q = (
            select(Opportunity, Organization, User, next_due.c.due, email)
            .outerjoin(Organization, Organization.id == Opportunity.organization_id)
            .outerjoin(LeadRecord, LeadRecord.dedupe_key == Opportunity.lead_key)
            .outerjoin(User, User.id == Opportunity.owner_id)
            .outerjoin(next_due, next_due.c.opportunity_id == Opportunity.id)
        )
        if owner == "me":
            q = q.where(Opportunity.owner_id == me.id)
        reply_counts = dict(db.execute(select(Opportunity.reply_status, func.count()).where(
            Opportunity.reply_status.is_not(None)).group_by(Opportunity.reply_status)).all())
        reply = reply if reply in REPLY_STATUSES else ""
        if reply:
            q = q.where(Opportunity.reply_status == reply)
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
        "reply": reply, "reply_counts": reply_counts, "reply_status_names": mailboxes.REPLY_STATUS_NAMES,
    })


def _templates_for(db, user: User | None) -> list[LetterTemplate]:
    """İstifadəçinin öz şablonları + komandaya paylaşılanlar."""
    if user is None:
        return []
    return list(db.scalars(select(LetterTemplate).where(
        (LetterTemplate.user_id == user.id) | LetterTemplate.is_shared.is_(True)).order_by(LetterTemplate.name)))


def _opp_context(db, opp: Opportunity, me: User | None = None) -> dict:
    lead = db.get(LeadRecord, opp.lead_key) if opp.lead_key else None
    users = _active_users(db)
    contacts = contacts_for(db, opp.lead_key, opp.organization_id)
    org = db.get(Organization, opp.organization_id) if opp.organization_id else None
    default = mailboxes.default_sender()
    return {
        "emails": opportunity_emails(lead, org, contacts),
        "mailbox_list": mailboxes.active(db), "default_sender": default, "templates": _templates_for(db, me),
        "approvals": list(db.scalars(select(LetterApproval).where(LetterApproval.opportunity_id == opp.id)
                                     .order_by(LetterApproval.created_at.desc()))),
        "replies": list(db.scalars(select(InboundEmail).where(InboundEmail.opportunity_id == opp.id)
                                   .order_by(InboundEmail.received_at.desc()))),
        "reply_status_names": mailboxes.REPLY_STATUS_NAMES, "sentiment_names": mailboxes.SENTIMENT_NAMES,
        "approval_names": APPROVAL_NAMES,
        "active": "opportunities", "opp": opp, "lead": lead,
        "org": org,
        "tender": db.get(Tender, opp.tender_id) if opp.tender_id else None,
        "contacts": contacts, "contact_sources": CONTACT_SOURCE_NAMES,
        "email_contacts": [c for c in contacts if c.email],
        "outreach": list(db.scalars(select(Outreach).where(Outreach.opportunity_id == opp.id)
                                    .order_by(Outreach.created_at.desc()))),
        "outreach_kinds": OUTREACH_KIND_NAMES, "languages": LANGUAGES,
        "default_language": detect_language(lead.raw_text if lead else opp.title),
        "smtp_ready": mail.smtp_configured(), "leaders_ready": leaders.enabled(),
        "tasks": crm.opp_tasks(db, opp.id), "users": {u.id: u for u in users}, "user_list": users,
        "owner": db.get(User, opp.owner_id) if opp.owner_id else None,  # deaktiv olsa da göstərilir
        "stages": crm.pipeline_for(opp), "stage_names": crm.STAGE_NAMES,
        "lost_reasons": crm.LOST_REASON_NAMES, "task_status_names": crm.TASK_STATUS_NAMES,
        "priority_names": crm.PRIORITY_NAMES,
    }


APPROVAL_NAMES = {"pending": "Təsdiq gözləyir", "approved": "Təsdiqləndi", "rejected": "Rədd edildi"}


@router.get("/opportunities/{opp_id}")
def opportunity_page(request: Request, opp_id: int, approval: str = "", me: User = Depends(REQUIRE["viewer"])):
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        context = _opp_context(db, opp, me)
        # Təsdiqlənmiş məktub → göndərmə formu onunla doldurulur (fayl əlavə edib "Göndər").
        ok = db.get(LetterApproval, int(approval)) if approval.isdigit() else None
        if ok is not None and ok.opportunity_id == opp.id and ok.status == "approved" and ok.sent_at is None \
                and security.has_role(me, "sales"):
            context["draft"] = {"to": ok.to_address, "subject": ok.subject, "body": ok.body,
                                "language": ok.language or "az", "contact_id": ok.contact_id or "",
                                "mailbox_id": ok.mailbox_id or "", "approval_id": ok.id,
                                "send_key": secrets.token_urlsafe(24)}
    return _render(request, "opportunity.html", context)


# --- Email (AI qaralama → insan yoxlayır → şirkət poçtu SMTP) -------------------------

OUTREACH_KIND_NAMES = {"email": "Email", "linkedin_comment": "LinkedIn şərhi"}


def _template(db, template_id: str, me: User) -> LetterTemplate | None:
    tpl = db.get(LetterTemplate, int(template_id)) if template_id.isdigit() else None
    return tpl if tpl is not None and (tpl.user_id == me.id or tpl.is_shared) else None


@router.post("/opportunities/{opp_id}/email/draft", dependencies=[Depends(verify_csrf)])
def opportunity_email_draft(request: Request, opp_id: int, contact_id: str = Form(""), language: str = Form("az"),
                            template_id: str = Form(""), mailbox_id: str = Form(""), instructions: str = Form(""),
                            to: str = Form(""), subject: str = Form(""), body: str = Form(""),
                            send_key: str = Form(""), approval_id: str = Form(""),
                            me: User = Depends(REQUIRE["sales"])):
    """AI qaralaması. `instructions` doludursa — "Yenidən yarat": mövcud qaralama istifadəçinin
    göstərişi ilə yenidən yazılır (mövzu, mətn, alıcı formda qalır)."""
    path = f"/opportunities/{opp_id}"
    language = language if language in LANGUAGES else "az"
    instructions = instructions.strip()
    revising = bool(body.strip())
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        context = _opp_context(db, opp, me)
        contact = db.get(Contact, int(contact_id)) if contact_id.isdigit() else None
        # "to:<email>" — kontakt olmayan ünvan (təşkilatın / elanın emaili), yalnız bu fürsətinkilərdən.
        picked = contact_id.removeprefix("to:").lower() if contact_id.startswith("to:") else ""
        if picked and not to.strip() and any(e["email"] == picked for e in context["emails"]):
            to = picked
        tpl = _template(db, template_id, me)
        sender = mailboxes.resolve(db, mailbox_id)
        lead, org = context["lead"], context["org"]
        current = {"to": to.strip(), "subject": " ".join(subject.split()), "body": body.strip(), "language": language,
                   "contact_id": contact.id if contact else "", "template_id": tpl.id if tpl else "",
                   "mailbox_id": mailbox_id if mailbox_id.isdigit() else "",
                   "approval_id": approval_id if approval_id.isdigit() else "",
                   "send_key": send_key if SEND_KEY_RE.fullmatch(send_key) else secrets.token_urlsafe(24)}
        if revising and not instructions:
            return _render(request, "opportunity.html", {
                **context, "draft": current, "page_error": "Nəyi dəyişmək lazımdır? Göstərişi yaz, sonra «Yenidən yarat» bas"})
        try:
            draft = mail.draft_email(
                lead_title=lead.title if lead else opp.title, lead_text=lead.raw_text if lead else "",
                org_name=org.name if org else (lead.entity_name if lead else None),
                contact_name=contact.name if contact else None, contact_title=contact.title if contact else None,
                sender_name=me.full_name, language=language, template=tpl.body if tpl else None,
                previous={"subject": current["subject"], "body": current["body"]} if revising else None,
                instructions=instructions or None,
                company_name=sender.name if sender is not None and sender.mailbox_id else None)
        except OutreachError as exc:
            if revising:
                return _render(request, "opportunity.html", {**context, "draft": current, "page_error": str(exc)})
            return _go(path, err=str(exc))
    draft = {**current, "subject": draft["subject"], "body": mail.with_signature(draft["body"], me.full_name),
             "to": current["to"] or (contact.email if contact and contact.email else ""), "revised": revising,
             # Mətn dəyişdi — əvvəlki təsdiq artıq bu məktuba aid deyil.
             "approval_id": ""}
    return _render(request, "opportunity.html", {**context, "draft": draft})


SEND_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# Vercel funksiyasına sorğu ən çox 4.5 MB ola bilər — fayllar + forma bunun içinə sığmalıdır.
MAX_ATTACHMENTS = 5
MAX_ATTACHMENTS_BYTES = 4 * 1024 * 1024
BLOCKED_EXTENSIONS = {"exe", "bat", "cmd", "com", "js", "vbs", "scr", "msi", "jar", "ps1", "sh"}  # Gmail da bloklayır


def _read_attachments(files: list[UploadFile]) -> list[tuple[str, bytes, str]]:
    """Formdan gələn fayllar → (ad, məzmun, MIME). Boş fayl sahəsi atlanır; qayda pozulsa CrmError."""
    out, total = [], 0
    for f in files or []:
        name = (f.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()[:120]
        if not name:
            continue
        if name.rsplit(".", 1)[-1].lower() in BLOCKED_EXTENSIONS:
            raise crm.CrmError(f"Bu növ fayl göndərilə bilməz: {name}")
        data = f.file.read(MAX_ATTACHMENTS_BYTES + 1)
        total += len(data)
        if total > MAX_ATTACHMENTS_BYTES:
            raise crm.CrmError("Fayllar birlikdə 4 MB-dan böyük ola bilməz")
        if data:
            out.append((name, data, f.content_type or "application/octet-stream"))
    if len(out) > MAX_ATTACHMENTS:
        raise crm.CrmError(f"Ən çox {MAX_ATTACHMENTS} fayl əlavə etmək olar")
    return out


@router.post("/opportunities/{opp_id}/email/send", dependencies=[Depends(verify_csrf)])
def opportunity_email_send(request: Request, opp_id: int, to: str = Form(...), subject: str = Form(...),
                           body: str = Form(...), language: str = Form(""), contact_id: str = Form(""),
                           send_key: str = Form(""), mailbox_id: str = Form(""), approval_id: str = Form(""),
                           template_id: str = Form(""), files: list[UploadFile] = File(default=[]),
                           me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    # Sətir sonu mövzuda başlıq inyeksiyası / SMTP xətası (500) verirdi.
    to, subject, body = to.strip().lower(), " ".join(subject.split()), body.strip()

    def back_to_draft(error: str):
        # Yoxlama xətasında redaktə olunmuş mətn itməsin — qaralama formu doldurulmuş qalır.
        with security.SessionLocal() as db:
            opp = db.get(Opportunity, opp_id)
            if opp is None:
                return _go("/opportunities", err="Fürsət tapılmadı")
            context = _opp_context(db, opp, me)
        draft = {"to": to, "subject": subject, "body": body, "language": language[:2],
                 "contact_id": contact_id if contact_id.isdigit() else "", "send_key": send_key,
                 "mailbox_id": mailbox_id if mailbox_id.isdigit() else "",
                 "approval_id": approval_id if approval_id.isdigit() else "",
                 "template_id": template_id if template_id.isdigit() else ""}
        return _render(request, "opportunity.html", {**context, "draft": draft, "page_error": error})

    if not EMAIL_RE.fullmatch(to) or len(to) > 254:
        return back_to_draft("Alıcının email ünvanı yanlışdır")
    if not subject or len(body) < 20:
        return back_to_draft("Mövzu və məktub mətni boş ola bilməz")
    if not SEND_KEY_RE.fullmatch(send_key):
        return _go(path, err="Forma köhnəlib — məktubu yenidən yaradın")
    try:
        attachments = _read_attachments(files)
    except crm.CrmError as exc:
        return back_to_draft(str(exc))
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        sender = mailboxes.resolve(db, mailbox_id)
        if mailbox_id.isdigit() and sender is None:
            return back_to_draft("Seçilmiş göndərən poçt tapılmadı və ya söndürülüb")
        contact = db.get(Contact, int(contact_id)) if contact_id.isdigit() else None
        approval = db.get(LetterApproval, int(approval_id)) if approval_id.isdigit() else None
        # Göndərməzdən əvvəl sətir rezerv olunur: ikinci klik eyni send_key-ə dəyib dayanır.
        row = Outreach(kind="email", lead_key=opp.lead_key, opportunity_id=opp.id,
                       contact_id=contact.id if contact else None, to_address=to, subject=subject[:200],
                       body=body, language=language[:2] or None, status="failed", error="Göndərilir…",
                       user_id=me.id, created_at=datetime.utcnow(), send_key=send_key,
                       attachments="\n".join(name for name, _, _ in attachments) or None,
                       mailbox_id=sender.mailbox_id if sender is not None else None)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return _go(path, msg="Bu məktub artıq göndərilib")
        # Cavab şirkət poçtuna gəlsin ki, panel onu oxuyub fürsətdə göstərsin; poçt oxunmursa — əməkdaşın özünə.
        reply_to = None if sender is not None and sender.can_receive else me.email
        try:
            message_id = mail.send_email(to=to, subject=subject[:200], body=body, reply_to=reply_to,
                                         attachments=attachments, sender=sender)
        except OutreachError as exc:
            row.error = str(exc)[:500]
            db.commit()
            return _go(path, err=str(exc))
        if approval is not None and approval.opportunity_id == opp.id and approval.status == "approved":
            approval.sent_at = datetime.utcnow()
        crm.email_sent(db, opp, to=to, subject=subject[:200], body=body, language=language[:2] or None,
                       contact_id=contact.id if contact else None, user_id=me.id, message_id=message_id, row=row)
    who = f" ({sender.name})" if sender is not None and sender.mailbox_id else ""
    return _go(path, msg=f"Məktub göndərildi{who}: {to}. Cavab gələnə qədər status — Gözləmədə")


# --- Təsdiqə göndər (Təsdiqləyici rolu, app/approval_routes.py) ------------------------


def approvers(db) -> list[User]:
    """Təsdiqləyicilər; heç biri yoxdursa — adminlər (sorğu itməsin)."""
    rows = list(db.scalars(select(User).where(User.role_code == "approver", User.is_active.is_(True))))
    return rows or list(db.scalars(select(User).where(User.role_code == "admin", User.is_active.is_(True))))


@router.post("/opportunities/{opp_id}/email/approval", dependencies=[Depends(verify_csrf)])
def opportunity_email_approval(request: Request, opp_id: int, to: str = Form(""), subject: str = Form(""),
                               body: str = Form(""), language: str = Form(""), contact_id: str = Form(""),
                               mailbox_id: str = Form(""), send_key: str = Form(""), template_id: str = Form(""),
                               me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    to, subject, body = to.strip().lower(), " ".join(subject.split()), body.strip()
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        error = None
        if not EMAIL_RE.fullmatch(to) or len(to) > 254:
            error = "Alıcının email ünvanı yanlışdır"
        elif not subject or len(body) < 20:
            error = "Mövzu və məktub mətni boş ola bilməz"
        if error:
            draft = {"to": to, "subject": subject, "body": body, "language": language[:2],
                     "contact_id": contact_id if contact_id.isdigit() else "",
                     "mailbox_id": mailbox_id if mailbox_id.isdigit() else "",
                     "template_id": template_id if template_id.isdigit() else "",
                     "send_key": send_key if SEND_KEY_RE.fullmatch(send_key) else secrets.token_urlsafe(24)}
            return _render(request, "opportunity.html", {**_opp_context(db, opp, me), "draft": draft, "page_error": error})
        contact = db.get(Contact, int(contact_id)) if contact_id.isdigit() else None
        sender = mailboxes.resolve(db, mailbox_id) if mailbox_id.isdigit() else None
        row = LetterApproval(opportunity_id=opp.id, requested_by_id=me.id, to_address=to, subject=subject[:200],
                             body=body, language=language[:2] or None, contact_id=contact.id if contact else None,
                             mailbox_id=sender.mailbox_id if sender is not None else None, status="pending",
                             created_at=datetime.utcnow())
        db.add(row)
        db.flush()
        for user in approvers(db):
            if user.id != me.id:
                notify(db, user.id, f"Təsdiq gözləyir: {me.full_name} — {subject[:120]}", f"/approvals#a{row.id}",
                       commit=False)
        db.commit()
    return _go(path + "#approvals", msg="Məktub təsdiqə göndərildi — cavab bildiriş kimi gələcək")


# --- Məktuba cavablar və status ----------------------------------------------------------


@router.post("/opportunities/{opp_id}/reply-status", dependencies=[Depends(verify_csrf)])
def opportunity_reply_status(opp_id: int, status: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    if status and status not in REPLY_STATUSES:
        return _go(path, err="Yanlış status")
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        opp.reply_status = status or None
        opp.updated_at = datetime.utcnow()
        db.commit()
    return _go(path, msg=f"Status: {mailboxes.REPLY_STATUS_NAMES.get(status, 'yoxdur')}")


@router.post("/mail/check", dependencies=[Depends(verify_csrf)])
def mail_check(back: str = Form("/opportunities"), me: User = Depends(REQUIRE["sales"])):
    """"Cavabları yoxla": bütün şirkət poçtlarının INBOX-u oxunur (məktublar oxunmamış qalır)."""
    back = back if back.startswith("/opportunities") and not back.startswith("//") and "\\" not in back else "/opportunities"
    with security.SessionLocal() as db:
        added, errors = mailboxes.poll_all(db)
    if errors and not added:
        return _go(back, err="; ".join(errors)[:400])
    msg = f"{added} yeni cavab" if added else "Yeni cavab yoxdur"
    return _go(back, msg=msg + (f" (xəta: {'; '.join(errors)[:200]})" if errors else ""))


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
            value = Decimal(expected_value.replace(",", ".").replace(" ", "")) if expected_value.strip() else None
        except InvalidOperation:
            return _go(path, err="Gözlənilən dəyər rəqəm olmalıdır")
        # Numeric(14, 2): NaN/Infinity və 12 rəqəmdən böyük dəyər bazada overflow (500) verirdi.
        if value is not None and (not value.is_finite() or value < 0 or value >= MAX_EXPECTED_VALUE):
            return _go(path, err="Gözlənilən dəyər 0 ilə 999 999 999 999 arasında olmalıdır")
        opp.expected_value = value.quantize(Decimal("0.01")) if value is not None else None
        try:
            opp.expected_close_on = datetime.fromisoformat(expected_close_on).date() if expected_close_on else None
        except ValueError:
            return _go(path, err="Bağlanma tarixi yanlışdır")
        new_owner = int(owner_id) if owner_id.isdigit() else None
        if new_owner != opp.owner_id:
            # Başqasına təhvil vermək menecer işidir; satış yalnız özünə götürə bilər.
            if not security.has_role(me, "manager") and new_owner != me.id:
                return _go(path, err="Məsul şəxsi yalnız menecer dəyişə bilər")
            target = db.get(User, new_owner) if new_owner is not None else None
            if new_owner is not None and (target is None or not target.is_active):
                return _go(path, err="İstifadəçi tapılmadı")
            opp.owner_id = new_owner
        opp.updated_at = datetime.utcnow()
        db.commit()
    return _go(path, msg="Yadda saxlandı")


@router.post("/opportunities/{opp_id}/tasks", dependencies=[Depends(verify_csrf)])
def opportunity_add_task(opp_id: int, title: str = Form(...), due_on: str = Form(""),
                         assignee_id: str = Form(""), priority: str = Form("normal"),
                         description: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        assignee = int(assignee_id) if assignee_id.isdigit() else me.id
        target = db.get(User, assignee)
        if target is None or not target.is_active:
            return _go(path, err="Məsul istifadəçi tapılmadı")
        try:
            crm.add_task(db, opp, title, crm.parse_due(due_on), assignee, me.id, priority, description)
        except crm.CrmError as exc:
            return _go(path, err=str(exc))
    return _go(path, msg="Tapşırıq əlavə olundu")


@router.post("/opportunities/{opp_id}/linkedin-comment", dependencies=[Depends(verify_csrf)])
def opportunity_linkedin_comment(opp_id: int, me: User = Depends(REQUIRE["sales"])):
    """Alınmamış avtomatik şərhi təkrarlamaq ("Yenidən cəhd et")."""
    path = f"/opportunities/{opp_id}"
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        if not linkedin_comment.enabled():
            return _go(path, err="Avtomatik LinkedIn şərhi söndürülüb (LINKEDIN_AUTOCOMMENT)")
        row = linkedin_comment.auto_comment(db, opp, user_id=me.id, retry=True)
    if row is None:
        return _go(path, err="Bu posta artıq şərh yazılıb və ya bu fürsət LinkedIn postundan deyil")
    if row.status != "sent":
        return _go(path, err=f"Şərh yazılmadı: {row.error}")
    return _go(path, msg="LinkedIn şərhi yazıldı")


# --- Rəhbərlərin LinkedIn profilləri (Apify, app/leaders.py) -------------------------


@router.post("/opportunities/{opp_id}/leaders", dependencies=[Depends(verify_csrf)])
def opportunity_leaders(opp_id: int, force: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    """JSON: fürsət səhifəsi ilk açılanda JS özü çağırır; "Yenidən axtar" düyməsi force=1 göndərir."""
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return JSONResponse({"error": "Fürsət tapılmadı"}, status_code=404)
        if opp.leaders_fetched_at is not None and force != "1":
            return {"added": 0, "skipped": True}
        try:
            added = leaders.fetch_for(db, opp)
        except leaders.LeadersError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
    return {"added": added}


# --- Kontaktlar ------------------------------------------------------------------

CONTACT_SOURCE_NAMES = {"text": "elan mətni", "ai": "AI (mətndən)", "author": "post müəllifi",
                        "website": "şirkət saytı", "enrichment": "axtarış", "manual": "əl ilə"}


@router.post("/opportunities/{opp_id}/contacts", dependencies=[Depends(verify_csrf)])
def opportunity_add_contact(opp_id: int, name: str = Form(""), title: str = Form(""), email: str = Form(""),
                            phone: str = Form(""), linkedin_url: str = Form(""), me: User = Depends(REQUIRE["sales"])):
    path = f"/opportunities/{opp_id}"
    email, linkedin_url = email.strip().lower(), linkedin_url.strip()
    if not (name.strip() or email or phone.strip() or linkedin_url):
        return _go(path, err="Ad, email, telefon və ya LinkedIn — ən azı biri lazımdır")
    if email and (len(email) > 254 or not EMAIL_RE.fullmatch(email)):  # RFC 5321: ən çox 254 simvol
        return _go(path, err="Email formatı yanlışdır")
    if linkedin_url and not linkedin_url.startswith("https://"):
        return _go(path, err="LinkedIn linki https:// ilə başlamalıdır")
    with security.SessionLocal() as db:
        opp = db.get(Opportunity, opp_id)
        if opp is None:
            return _go("/opportunities", err="Fürsət tapılmadı")
        db.add(Contact(organization_id=opp.organization_id, lead_key=opp.lead_key, name=name.strip()[:200] or None,
                       title=title.strip()[:200] or None, email=email or None,
                       phone=normalize_phone(phone) or phone.strip()[:32] or None,
                       linkedin_url=linkedin_url or None, source="manual"))
        db.commit()
    return _go(path, msg="Kontakt əlavə olundu")


@router.post("/contacts/{contact_id}/delete", dependencies=[Depends(verify_csrf)])
def contact_delete(contact_id: int, back: str = Form("/opportunities"), me: User = Depends(REQUIRE["sales"])):
    back = back if back.startswith("/opportunities/") and not back.startswith("//") else "/opportunities"
    with security.SessionLocal() as db:
        contact = db.get(Contact, contact_id)
        if contact is None:
            return _go(back, err="Kontakt tapılmadı")
        if db.scalar(select(func.count()).select_from(Outreach).where(Outreach.contact_id == contact_id)):
            return _go(back, err="Bu kontakta məktub göndərilib — tarixçə üçün silinmir")
        db.delete(contact)
        db.commit()
    return _go(back, msg="Kontakt silindi")


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
        rows = db.execute(q.order_by(Task.due_on.is_(None), Task.due_on, Task.id)).all()
    return _render(request, "tasks.html", {
        "active": "tasks", "rows": rows, "who": who, "status": status, "today": crm.today(),
        "task_status_names": crm.TASK_STATUS_NAMES, "priority_names": crm.PRIORITY_NAMES,
    })


@router.post("/tasks/{task_id}/status", dependencies=[Depends(verify_csrf)])
def task_status(task_id: int, status: str = Form(...), back: str = Form("/tasks"),
                me: User = Depends(REQUIRE["sales"])):
    back = back if back.startswith("/") and not back.startswith("//") and "\\" not in back else "/tasks"
    with security.SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            return _go(back, err="Tapşırıq tapılmadı")
        try:
            crm.set_task_status(db, task, status)
        except crm.CrmError as exc:
            return _go(back, err=str(exc))
    return _go(back, msg=f"Tapşırıq: {crm.TASK_STATUS_NAMES[status]}")
