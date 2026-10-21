"""Ayarlar → Poçt qutuları (yalnız admin) və cavabları yoxlayan cron endpoint-i (app/mailboxes.py)."""
from __future__ import annotations

import hmac
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import mailboxes
from app.auth import security
from app.auth.security import REQUIRE, verify_csrf
from app.contacts import EMAIL_RE
from app.crm_routes import _go, _render
from app.db.models import Mailbox, User

router = APIRouter()


@router.get("/settings/mailboxes")
def mailboxes_page(request: Request, me: User = Depends(REQUIRE["admin"])):
    with security.SessionLocal() as db:
        rows = list(db.scalars(select(Mailbox).order_by(Mailbox.is_active.desc(), Mailbox.name)))
    return _render(request, "mailboxes.html", {
        "active": "mailboxes", "rows": rows, "default": mailboxes.default_sender(), "env_name": mailboxes.password_env,
        "has_password": {mb.id: bool(os.environ.get(mailboxes.password_env(mb.key))) for mb in rows},
    })


def _port(value: str, default: int) -> int:
    value = value.strip()
    if not value:
        return default
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError("Port 1–65535 arasında rəqəm olmalıdır")
    return int(value)


@router.post("/settings/mailboxes", dependencies=[Depends(verify_csrf)])
def mailboxes_save(key: str = Form(""), name: str = Form(""), email: str = Form(""), smtp_host: str = Form(""),
                   smtp_port: str = Form(""), imap_host: str = Form(""), imap_port: str = Form(""),
                   username: str = Form(""), mailbox_id: str = Form(""), me: User = Depends(REQUIRE["admin"])):
    key, email = key.strip().lower(), email.strip().lower()
    name = " ".join(name.split())[:120]
    if not mailboxes.KEY_RE.match(key):
        return _go("/settings/mailboxes", err="Açar: kiçik latın hərfləri, rəqəm və _ (2–32 simvol), məs. seaport")
    if not name or not EMAIL_RE.fullmatch(email):
        return _go("/settings/mailboxes", err="Şirkətin adı və düzgün email lazımdır")
    try:
        sp, ip = _port(smtp_port, 587), _port(imap_port, 993)
    except ValueError as exc:
        return _go("/settings/mailboxes", err=str(exc))
    with security.SessionLocal() as db:
        mb = db.get(Mailbox, int(mailbox_id)) if mailbox_id.isdigit() else None
        clash = db.scalar(select(Mailbox.id).where(Mailbox.key == key))
        if clash and (mb is None or clash != mb.id):
            return _go("/settings/mailboxes", err=f"«{key}» açarı artıq var")
        if mb is None:
            mb = Mailbox(key=key, is_active=True, created_at=datetime.utcnow())
            db.add(mb)
        mb.key, mb.name, mb.email = key, name, email
        mb.smtp_host, mb.smtp_port = smtp_host.strip()[:255] or None, sp
        mb.imap_host, mb.imap_port = imap_host.strip()[:255] or None, ip
        mb.username = username.strip()[:255] or None
        db.commit()
    hint = "" if os.environ.get(mailboxes.password_env(key)) else f" Şifrəni env-də təyin et: {mailboxes.password_env(key)}"
    return _go("/settings/mailboxes", msg=f"{name} yadda saxlandı.{hint}")


@router.post("/settings/mailboxes/{mb_id}/active", dependencies=[Depends(verify_csrf)])
def mailboxes_toggle(mb_id: int, me: User = Depends(REQUIRE["admin"])):
    with security.SessionLocal() as db:
        mb = db.get(Mailbox, mb_id)
        if mb is None:
            return _go("/settings/mailboxes", err="Poçt qutusu tapılmadı")
        mb.is_active = not mb.is_active
        db.commit()
        state = "aktiv" if mb.is_active else "söndürüldü"
    return _go("/settings/mailboxes", msg=f"{mb.name}: {state}")


@router.api_route("/api/cron/mail", methods=["GET", "POST"])
def cron_mail(request: Request):
    """Planlı yoxlama (Modal tick / GitHub Actions / Vercel Cron): Bearer TRIGGER_TOKEN və ya CRON_SECRET."""
    sent = request.headers.get("authorization", "")
    tokens = [t for t in (os.environ.get("TRIGGER_TOKEN"), os.environ.get("CRON_SECRET")) if t]
    if not tokens or not any(hmac.compare_digest(sent, f"Bearer {t}") for t in tokens):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with security.SessionLocal() as db:
        added, errors = mailboxes.poll_all(db)
    return JSONResponse({"added": added, "errors": errors})
