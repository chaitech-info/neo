from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.db.models import ROLE_CODES, User


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(session: Session, *, email: str, full_name: str, role_code: str, password: str) -> User:
    """İstifadəçi yaradır. Xəta mesajları CLI-də olduğu kimi göstərilir."""
    email = normalize_email(email)
    if "@" not in email:
        raise ValueError("Email düzgün deyil")
    if role_code not in ROLE_CODES:
        raise ValueError(f"Rol bunlardan biri olmalıdır: {', '.join(ROLE_CODES)}")
    if not full_name.strip():
        raise ValueError("Ad boş ola bilməz")
    if session.execute(select(User.id).where(User.email == email)).first():
        raise ValueError(f"{email} artıq mövcuddur")

    user = User(
        email=email,
        full_name=full_name.strip(),
        role_code=role_code,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user
