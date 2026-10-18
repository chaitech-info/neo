"""Giriş/çıxış, ilk quraşdırma (ilk admin) və istifadəçilərin idarəsi (admin)."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.auth import security
from app.auth.passwords import MIN_LENGTH, hash_password, verify_password
from app.auth.security import REQUIRE, ROLE_NAMES, verify_csrf
from app.auth.users import create_user, normalize_email
from app.db.models import ROLE_CODES, User

router = APIRouter()


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return request.app.state.templates.TemplateResponse(
        request, name, {"request": request, **context}, status_code=status_code
    )


def _safe_next(target: str | None) -> str:
    # Yalnız öz saytımızın yolu — "//evil.com" və tam URL-lər açıq yönləndirmə olardı.
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


def _go(path: str, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    if msg:
        path += f"?msg={quote(msg)}"
    elif err:
        path += f"?err={quote(err)}"
    return RedirectResponse(url=path, status_code=303)


# --- Giriş / çıxış -------------------------------------------------------------

_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password("olmayan-hesab-üçün-şifrə"))
    return _DUMMY[0]



@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if security.user_count() == 0:
        return _go("/setup")
    if security.load_user(request):
        return _go(_safe_next(next))
    return _render(request, "login.html", {"next": _safe_next(next), "active": "login"})


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    email = normalize_email(email)
    ip = security.client_ip(request)
    if security.is_locked(email, ip):
        return _render(request, "login.html", {
            "next": _safe_next(next), "email": email, "active": "login",
            "error": "Çox sayda səhv cəhd. 15 dəqiqədən sonra yenidən yoxla.",
        }, status_code=429)

    with security.SessionLocal() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            # Olmayan hesabda da hash hesablanır — cavab vaxtından email-in mövcudluğu bilinməsin.
            verify_password(password, _dummy_hash())
        ok = user is not None and user.is_active and verify_password(password, user.password_hash)
        if ok:
            user.last_login_at = datetime.utcnow()
            db.commit()

    if not ok:
        security.record_failure(email, ip)
        return _render(request, "login.html", {
            "next": _safe_next(next), "email": email, "active": "login",
            "error": "Email və ya şifrə səhvdir.",
        }, status_code=401)

    security.clear_failures(email, ip)
    security.login(request, user)
    return _go(_safe_next(next))


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout_submit(request: Request):
    security.logout(request)
    return _go("/login", msg="Çıxış etdin")


# --- İlk quraşdırma: bazada heç bir istifadəçi yoxdursa ilk admin -----------------


@router.get("/setup")
def setup_page(request: Request):
    if security.user_count() > 0:
        return _go("/login")
    return _render(request, "setup.html", {"active": "setup", "min_length": MIN_LENGTH})


@router.post("/setup", dependencies=[Depends(verify_csrf)])
def setup_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
):
    if security.user_count() > 0:  # iki nəfər eyni anda göndərsə — ikincisi admin olmur
        return _go("/login")
    context = {"active": "setup", "min_length": MIN_LENGTH, "full_name": full_name, "email": email}
    if password != password2:
        return _render(request, "setup.html", {**context, "error": "Şifrələr uyğun gəlmir."}, 400)
    with security.SessionLocal() as db:
        try:
            user = create_user(db, email=email, full_name=full_name, role_code="admin", password=password)
        except ValueError as exc:
            return _render(request, "setup.html", {**context, "error": str(exc)}, 400)
    security.login(request, user)
    return _go("/", msg=f"Xoş gəldin, {user.full_name}. Admin hesabı yaradıldı.")


# --- İstifadəçilər (yalnız admin) ------------------------------------------------


def _active_admins(db) -> int:
    return db.execute(
        select(func.count(User.id)).where(User.role_code == "admin", User.is_active.is_(True))
    ).scalar_one()


@router.get("/users")
def users_page(request: Request, me: User = Depends(REQUIRE["admin"])):
    with security.SessionLocal() as db:
        users = db.execute(select(User).order_by(User.is_active.desc(), User.full_name)).scalars().all()
    return _render(request, "users.html", {
        "active": "users", "users": users, "roles": [(c, ROLE_NAMES[c]) for c in ROLE_CODES],
        "role_names": ROLE_NAMES, "min_length": MIN_LENGTH, "me": me,
    })


@router.post("/users", dependencies=[Depends(verify_csrf)])
def users_create(
    full_name: str = Form(...),
    email: str = Form(...),
    role_code: str = Form(...),
    password: str = Form(...),
    me: User = Depends(REQUIRE["admin"]),
):
    with security.SessionLocal() as db:
        try:
            user = create_user(db, email=email, full_name=full_name, role_code=role_code, password=password)
        except ValueError as exc:
            return _go("/users", err=str(exc))
    return _go("/users", msg=f"{user.email} yaradıldı ({ROLE_NAMES[user.role_code]})")


@router.post("/users/{user_id}/role", dependencies=[Depends(verify_csrf)])
def users_change_role(user_id: int, role_code: str = Form(...), me: User = Depends(REQUIRE["admin"])):
    if role_code not in ROLE_CODES:
        return _go("/users", err="Naməlum rol")
    with security.SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            return _go("/users", err="İstifadəçi tapılmadı")
        if user.role_code == "admin" and role_code != "admin" and user.is_active and _active_admins(db) <= 1:
            return _go("/users", err="Sonuncu aktiv admin-in rolunu dəyişmək olmaz — əvvəl başqa admin təyin et")
        user.role_code = role_code
        db.commit()
        return _go("/users", msg=f"{user.email}: rol → {ROLE_NAMES[role_code]}")


@router.post("/users/{user_id}/active", dependencies=[Depends(verify_csrf)])
def users_toggle_active(user_id: int, me: User = Depends(REQUIRE["admin"])):
    with security.SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            return _go("/users", err="İstifadəçi tapılmadı")
        if user.id == me.id:
            return _go("/users", err="Öz hesabını deaktiv edə bilməzsən")
        if user.is_active and user.role_code == "admin" and _active_admins(db) <= 1:
            return _go("/users", err="Sonuncu aktiv admin-i deaktiv etmək olmaz")
        user.is_active = not user.is_active
        db.commit()
        state = "aktiv edildi" if user.is_active else "deaktiv edildi (sessiyası bağlandı)"
        return _go("/users", msg=f"{user.email} {state}")


@router.post("/users/{user_id}/password", dependencies=[Depends(verify_csrf)])
def users_reset_password(user_id: int, password: str = Form(...), me: User = Depends(REQUIRE["admin"])):
    with security.SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            return _go("/users", err="İstifadəçi tapılmadı")
        try:
            user.password_hash = hash_password(password)
        except ValueError as exc:
            return _go("/users", err=str(exc))
        db.commit()
        return _go("/users", msg=f"{user.email}: şifrə dəyişdirildi (köhnə sessiyaları bağlandı)")
