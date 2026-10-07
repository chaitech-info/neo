"""Giriş, rollar və CSRF — admin panelin bütün səhifələri bunun arxasındadır.

Rollar iyerarxikdir: viewer < sales < manager < admin (qərar 2: bir istifadəçi,
bir rol). Hər route `Depends(REQUIRE[<rol>])` ilə qorunur; daxil olmayan
istifadəçi /login-ə, rolu çatmayan 403 səhifəsinə yönəldilir (app/web.py-dakı
exception handler-lər).

Sessiya imzalı cookie-dədir (SessionMiddleware): yalnız istifadəçi ID-si və
şifrə hash-inin son simvolları saxlanılır — şifrə dəyişəndə və ya istifadəçi
deaktiv ediləndə köhnə sessiyalar avtomatik etibarsız olur.
"""
# `from __future__ import annotations` QƏSDƏN yoxdur: FastAPI instance-dependency-nin (Require.__call__) mətn annotasiyasını həll edə bilmir və `request`-i query parametri sayıb 422 qaytarır.

import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import structlog
from fastapi import Request
from markupsafe import Markup
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.engine import engine
from app.db.models import User

log = structlog.get_logger()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

ROLE_RANK = {"viewer": 0, "sales": 1, "manager": 2, "admin": 3}
ROLE_NAMES = {"viewer": "Müşahidəçi", "sales": "Satış", "manager": "Menecer", "admin": "Administrator"}

SESSION_MAX_AGE = 12 * 3600
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60


SECRET_FILE = Path(__file__).resolve().parents[2] / ".session_secret"  # neo/.session_secret (gitignore-da)


def session_secret() -> str:
    """.env-dəki SESSION_SECRET; yoxdursa bir dəfə yaradılıb lokal fayla yazılır ki,
    server hər restartda (və --reload-da) hamını çıxarmasın. Production-da .env-də olmalıdır."""
    secret = os.environ.get("SESSION_SECRET")
    if secret:
        return secret
    if os.environ.get("VERCEL"):
        # Vercel-də fayla yazmaq olmur və hər instansiya fərqli açar yaradardı.
        raise RuntimeError("Vercel-də SESSION_SECRET təyin edilməyib (Project Settings → Environment Variables)")
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    secret = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(secret)
    SECRET_FILE.chmod(0o600)
    log.warning("session_secret_generated", path=str(SECRET_FILE), hint="production-da .env-də SESSION_SECRET təyin et")
    return secret


class LoginRequired(Exception):
    pass


class PermissionDenied(Exception):
    def __init__(self, required: str) -> None:
        super().__init__(required)
        self.required = required


def _marker(user: User) -> str:
    return user.password_hash[-16:]


def load_user(request: Request) -> Optional[User]:
    """Sessiyadakı istifadəçi (aktiv və şifrəsi dəyişməyibsə), əks halda None."""
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    uid = request.session.get("uid")
    if uid is not None:
        with SessionLocal() as db:
            candidate = db.get(User, uid)
        if candidate and candidate.is_active and hmac.compare_digest(
            request.session.get("pw", ""), _marker(candidate)
        ):
            user = candidate
        else:
            request.session.clear()
    request.state.user = user
    return user


def login(request: Request, user: User) -> None:
    request.session.clear()
    request.session.update({"uid": user.id, "pw": _marker(user), "csrf": secrets.token_urlsafe(32)})
    request.state.user = user


def logout(request: Request) -> None:
    request.session.clear()
    request.state.user = None


def has_role(user: Optional[User], role: str) -> bool:
    return user is not None and ROLE_RANK[user.role_code] >= ROLE_RANK[role]


class Require:
    """Route dependency: `Depends(REQUIRE["sales"])`. Instance-dır ki, testlər
    `app.dependency_overrides` ilə əvəz edə bilsin."""

    def __init__(self, role: str) -> None:
        self.role = role

    def __call__(self, request: Request) -> User:
        user = load_user(request)
        if user is None:
            raise LoginRequired()
        if not has_role(user, self.role):
            raise PermissionDenied(self.role)
        return user


REQUIRE = {role: Require(role) for role in ROLE_RANK}


# --- CSRF --------------------------------------------------------------------


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def csrf_field(request: Request) -> Markup:
    """Şablonlarda hər POST formunun içində: {{ csrf_field(request) }}."""
    return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token(request)}">')


class CsrfFailed(Exception):
    pass


async def verify_csrf(request: Request) -> None:
    form = await request.form()
    sent = str(form.get("csrf_token", ""))
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        raise CsrfFailed()


# --- Səhv giriş cəhdlərinin məhdudlaşdırılması ---------------------------------

_failures: dict[str, deque] = defaultdict(deque)


def is_locked(email: str, now: Optional[float] = None) -> bool:
    now = now or time.monotonic()
    attempts = _failures[email]
    while attempts and now - attempts[0] > LOCKOUT_SECONDS:
        attempts.popleft()
    return len(attempts) >= MAX_FAILED_LOGINS


def record_failure(email: str) -> None:
    _failures[email].append(time.monotonic())


def clear_failures(email: str) -> None:
    _failures.pop(email, None)


def user_count() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count(User.id))).scalar_one()
