"""Admin panel girişi, rollar və CSRF — real məntiqlə, müvəqqəti bazada."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import web
from app.auth import security
from app.auth.passwords import hash_password
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import LeadRecord, User
from app.pipeline import PipelineResult

pytestmark = pytest.mark.real_auth
PASSWORD = "möhkəm-şifrə-123"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'web.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    monkeypatch.setattr("app.auth.users.hash_password", lambda p: hash_password(p, iterations=1000))
    monkeypatch.setattr("app.auth.routes.hash_password", lambda p: hash_password(p, iterations=1000))
    security._failures.clear()
    yield factory
    engine.dispose()


def _user(db, email, role):
    with db() as s:
        return create_user(s, email=email, full_name=email.split("@")[0], role_code=role, password=PASSWORD)


def _csrf(client, path="/login"):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)[1]


def _login(client, email, password=PASSWORD, next_="/"):
    return client.post("/login", data={"email": email, "password": password, "next": next_,
                                       "csrf_token": _csrf(client)}, follow_redirects=False)


def _client():
    return TestClient(web.app)


def test_first_run_setup_creates_admin_once(db):
    c = _client()
    assert c.get("/", follow_redirects=False).headers["location"] == "/login?next=/"
    assert c.get("/login", follow_redirects=False).headers["location"] == "/setup"

    token = _csrf(c, "/setup")
    r = c.post("/setup", data={"full_name": "İlk Admin", "email": "Admin@Neo.az", "password": PASSWORD,
                               "password2": PASSWORD, "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?msg=")
    assert "İlk Admin" in c.get("/").text  # dərhal daxil olub

    with db() as s:
        assert s.execute(select(User.email, User.role_code)).all() == [("admin@neo.az", "admin")]
    assert _client().get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_wrong_password_and_lockout(db):
    _user(db, "a@neo.az", "admin")
    c = _client()
    r = _login(c, "a@neo.az", "səhv-şifrə-123")
    assert r.status_code == 401 and "səhvdir" in r.text
    for _ in range(4):
        _login(c, "a@neo.az", "səhv-şifrə-123")
    r = _login(c, "a@neo.az")  # düzgün şifrə, amma bloklanıb
    assert r.status_code == 429 and "15 dəqiqə" in r.text


def test_login_redirects_only_inside_site_and_updates_last_login(db):
    _user(db, "a@neo.az", "admin")
    assert _login(_client(), "a@neo.az", next_="//evil.com").headers["location"] == "/"
    assert _login(_client(), "a@neo.az", next_="/leads").headers["location"] == "/leads"
    with db() as s:
        assert s.execute(select(User.last_login_at)).scalar_one() is not None


def test_post_without_csrf_token_is_refused(db):
    _user(db, "a@neo.az", "admin")
    c = _client()
    _login(c, "a@neo.az")
    r = c.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert "a" in c.get("/").text  # hələ də daxildir


def test_api_requires_login(db):
    r = _client().get("/api/logs")
    assert r.status_code == 401 and r.json() == {"error": "login_required"}


def test_role_permissions(db, monkeypatch):
    for email, role in [("v@neo.az", "viewer"), ("s@neo.az", "sales"), ("m@neo.az", "manager"), ("a@neo.az", "admin")]:
        _user(db, email, role)
    with db() as s:
        s.add(LeadRecord(dedupe_key="k" * 64, source="oxu", source_url="https://oxu.az/1", title="T", raw_text="x",
                         category="event", scraped_at=__import__("datetime").datetime(2026, 9, 28), status="pending_review"))
        s.commit()

    class Fake:
        source_name = "oxu"
    monkeypatch.setattr(web, "SOURCES", {"oxu": ("Oxu.az", Fake)})
    monkeypatch.setattr(web, "run_pipeline", lambda *a, **k: PipelineResult(fresh=1, rejected=1))

    viewer = _client(); _login(viewer, "v@neo.az")
    home = viewer.get("/").text
    assert 'action="/scrape/' not in home and 'action="/scrape-all"' not in home and "İstifadəçilər" not in home
    assert "Uyğun deyil" not in viewer.get("/leads/pending_review").text
    r = viewer.post("/scrape/oxu", data={"csrf_token": _csrf(viewer, "/")})
    assert r.status_code == 403 and "Menecer" in r.text

    sales = _client(); _login(sales, "s@neo.az")
    assert "Uyğun deyil" in sales.get("/leads/pending_review").text
    assert sales.post("/scrape/oxu", data={"csrf_token": _csrf(sales, "/")}).status_code == 403
    r = sales.post(f"/leads/{'k' * 64}/reject", data={"csrf_token": _csrf(sales, "/")}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    with db() as s:
        lead = s.get(LeadRecord, "k" * 64)
        reviewer = s.execute(select(User.email).where(User.id == lead.reviewed_by_id)).scalar_one()
    assert lead.status == "rejected" and reviewer == "s@neo.az" and lead.reviewed_at is not None

    manager = _client(); _login(manager, "m@neo.az")
    assert 'action="/scrape/oxu"' in manager.get("/").text
    r = manager.post("/scrape/oxu", data={"csrf_token": _csrf(manager, "/")}, follow_redirects=False)
    assert "1 yeni lead" in __import__("urllib.parse").parse.unquote(r.headers["location"])
    assert manager.get("/users").status_code == 403

    admin = _client(); _login(admin, "a@neo.az")
    assert "İstifadəçilər" in admin.get("/").text and "v@neo.az" in admin.get("/users").text


def test_admin_manages_users_and_sessions_die(db):
    admin_id = _user(db, "a@neo.az", "admin").id
    admin = _client(); _login(admin, "a@neo.az")
    token = _csrf(admin, "/users")

    r = admin.post("/users", data={"full_name": "Satış Bir", "email": "sb@neo.az", "role_code": "sales",
                                   "password": PASSWORD, "csrf_token": token}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    with db() as s:
        sb = s.execute(select(User).where(User.email == "sb@neo.az")).scalar_one()

    other = _client(); _login(other, "sb@neo.az")
    assert other.get("/", follow_redirects=False).status_code == 200

    admin.post(f"/users/{sb.id}/password", data={"password": "yeni-şifrə-4567", "csrf_token": token})
    assert other.get("/", follow_redirects=False).status_code == 303  # şifrə dəyişdi → sessiya bağlandı
    assert _login(_client(), "sb@neo.az", "yeni-şifrə-4567").headers["location"] == "/"

    _login(other, "sb@neo.az", "yeni-şifrə-4567")
    admin.post(f"/users/{sb.id}/active", data={"csrf_token": token})
    assert other.get("/", follow_redirects=False).status_code == 303  # deaktiv → sessiya bağlandı
    assert _login(_client(), "sb@neo.az", "yeni-şifrə-4567").status_code == 401

    r = admin.post(f"/users/{admin_id}/active", data={"csrf_token": token}, follow_redirects=False)
    assert "err=" in r.headers["location"]  # öz hesabını deaktiv edə bilmir
    r = admin.post(f"/users/{admin_id}/role", data={"role_code": "sales", "csrf_token": token}, follow_redirects=False)
    assert "err=" in r.headers["location"]  # sonuncu admin
    with db() as s:
        assert s.get(User, admin_id).role_code == "admin"
