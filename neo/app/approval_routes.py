"""Təsdiq gözləyənlər (yalnız Təsdiqləyici və admin): satışın "Təsdiqə göndər" basdığı məktublar.

Təsdiqləyici mətni düzəldib təsdiqləyir və ya səbəb yazıb rədd edir; qərar göndərənə bildiriş kimi
gedir, status fürsət səhifəsinin "Təsdiq" bölməsində görünür. Təsdiqlənmiş məktubu göndərən özü
"Göndərməyə hazırla" ilə göndərir (fayllar təsdiqə getmir — göndərərkən əlavə olunur).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from app.auth import security
from app.auth.security import REQUIRE_APPROVER, verify_csrf
from app.crm_routes import APPROVAL_NAMES, _go, _render
from app.db.models import LetterApproval, Opportunity, User
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
    return _render(request, "approvals.html", {
        "active": "approvals", "pending": pending, "decided": decided, "approval_names": APPROVAL_NAMES,
    })


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
