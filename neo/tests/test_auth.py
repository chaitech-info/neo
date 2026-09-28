import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, verify_password
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head


def test_hash_roundtrip_and_no_plaintext():
    h = hash_password("düzgün-şifrə-123", iterations=1000)
    assert "düzgün" not in h and h.startswith("pbkdf2_sha256$1000$")
    assert verify_password("düzgün-şifrə-123", h)
    assert not verify_password("səhv-şifrə-123", h)
    assert not verify_password("düzgün-şifrə-123", "pozuq-hash")


def test_same_password_gets_different_salt():
    assert hash_password("eyni-şifrə-123", iterations=1000) != hash_password("eyni-şifrə-123", iterations=1000)


def test_short_password_rejected():
    with pytest.raises(ValueError):
        hash_password("qısa")


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.auth.users.hash_password", lambda p: hash_password(p, iterations=1000))
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    upgrade_to_head(engine)  # rollar miqrasiyadan gəlir
    with Session(engine) as s:
        yield s
    engine.dispose()


def test_create_user_normalizes_email_and_rejects_duplicates(session):
    user = create_user(session, email="  Ad@Sirket.AZ ", full_name="Ad Soyad", role_code="admin",
                       password="möhkəm-şifrə-1")
    assert user.email == "ad@sirket.az" and user.role_code == "admin" and user.is_active

    with pytest.raises(ValueError, match="artıq mövcuddur"):
        create_user(session, email="ad@sirket.az", full_name="X", role_code="sales", password="möhkəm-şifrə-2")


def test_create_user_rejects_unknown_role(session):
    with pytest.raises(ValueError, match="Rol"):
        create_user(session, email="b@sirket.az", full_name="B", role_code="superuser", password="möhkəm-şifrə-1")
