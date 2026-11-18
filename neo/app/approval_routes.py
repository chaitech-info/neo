"""Təsdiq gözləyənlər (yalnız CEO rolu): satışın "Təsdiqə göndər" basdığı məktublar.

Təsdiqləyici mətni düzəldib təsdiqləyir və ya səbəb yazıb rədd edir; qərar göndərənə bildiriş kimi
gedir, status fürsət səhifəsinin "Təsdiq" bölməsində görünür. Təsdiqlənmiş məktubu göndərən özü
"Göndərməyə hazırla" ilə göndərir. Məktuba qoşulmuş fayllar (təklif PDF-i, menyu) da təsdiqə gedir:
təsdiqləyici onları bu səhifədə açır, təsdiqdən sonra məktubla birlikdə göndərilir (letter_approval_files).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.auth import security
from app.auth.security import REQUIRE, REQUIRE_APPROVER, verify_csrf
from app.crm_routes import APPROVAL_NAMES, _go, _render, approval_files
from app.db.models import LetterApproval, LetterApprovalFile, Opportunity, User
from app.notify import notify

router = APIRouter()

DECIDED_SHOWN_DAYS = 14


@router.get("/approvals")
def approvals_page(request: Request, me: User = Depends(REQUIRE_APPROVER)):
    with security.SessionLocal() as db:
        pending = db.execute(
            select(LetterApproval, Opportunity, User)
            .join(Opportunity, Opportunity.id == LetterApproval.opportunity_id)
            .join(User, User.id == LetterApproval.requested_by_id)
            .where(LetterApproval.status == "pending").order_by(LetterApproval.created_at)).all()
        decided = db.execute(
            select(LetterApproval, Opportunity, User)
            .join(Opportunity, Opportunity.id == LetterApproval.opportunity_id)
            .join(User, User.id == LetterApproval.requested_by_id)
            .where(LetterApproval.status != "pending",
                   LetterApproval.decided_at >= datetime.utcnow() - timedelta(days=DECIDED_SHOWN_DAYS))
            .order_by(LetterApproval.decided_at.desc())).all()
        files = approval_files(db, [a.id for a, _, _ in pending + decided])
    return _render(request, "approvals.html", {
        "active": "approvals", "pending": pending, "decided": decided, "approval_names": APPROVAL_NAMES,
        "files": files,
    })


# Brauzerdə açılan növlər (tip uzantıdan təyin olunur, yükləyənin göndərdiyi tipdən yox); qalanları yüklənir.
INLINE_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}


@router.get("/approvals/files/{file_id}")
def approval_file(file_id: int, me: User = Depends(REQUIRE["sales"])):
    """Təsdiqə göndərilən məktubun faylı: CEO təsdiq səhifəsində, satış fürsət səhifəsində açır."""
    with security.SessionLocal() as db:
        row = db.get(LetterApprovalFile, file_id)
        if row is None:
            return Response("Fayl tapılmadı", status_code=404, media_type="text/plain; charset=utf-8")
        name, data = row.filename, row.data
    inline = INLINE_TYPES.get(name.rsplit(".", 1)[-1].lower())
    ascii_name = name.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "") or "fayl"
    disposition = f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(data, media_type=inline or "application/octet-stream", headers={
        "Content-Disposition": disposition, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.post("/approvals/{approval_id}/decide", dependencies=[Depends(verify_csrf)])
def approvals_decide(approval_id: int, decision: str = Form(...), note: str = Form(""), subject: str = Form(""),
                     body: str = Form(""), me: User = Depends(REQUIRE_APPROVER)):
    if decision not in ("approved", "rejected"):
        return _go("/approvals", err="Yanlış qərar")
    note = note.strip()[:1000]
    if decision == "rejected" and not note:
        return _go(f"/approvals#a{approval_id}", err="Rədd səbəbini yaz — göndərən nəyi düzəldəcəyini bilsin")
    with security.SessionLocal() as db:
        row = db.get(LetterApproval, approval_id)
        if row is None:
            return _go("/approvals", err="Sorğu tapılmadı")
        if row.status != "pending":
            return _go("/approvals", err="Bu məktub haqqında artıq qərar verilib")
        # Təsdiqləyici mətni düzəldə bilər — göndərən düzəldilmiş variantı göndərir.
        subject, body = " ".join(subject.split())[:200], body.strip()
        if decision == "approved" and subject and len(body) >= 20:
            row.subject, row.body = subject, body
        row.status, row.decision_note = decision, note or None
        row.decided_by_id, row.decided_at = me.id, datetime.utcnow()
        link = f"/opportunities/{row.opportunity_id}" + (f"?approval={row.id}#email" if decision == "approved"
                                                         else "#approvals")
        text = (f"Məktubun təsdiqləndi: {row.subject[:120]} — indi göndərə bilərsən" if decision == "approved"
                else f"Məktubun rədd edildi: {row.subject[:120]} — {note[:200]}")
        notify(db, row.requested_by_id, f"{text} ({me.full_name})", link, commit=False)
        db.commit()
    return _go("/approvals", msg=f"{APPROVAL_NAMES[decision]}: {subject or row.subject}"[:200])
