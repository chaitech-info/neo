"""Qiymət təklifi aləti: giriş yoxlaması, agent xəta xəritəsi, proxy route-ları, rol məhdudiyyəti."""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import offer_client as agent
from app import offer_routes, web
from app.auth import security
from app.auth.passwords import hash_password
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import LeadRecord, OfferRecord

OK = {"status": "ok", "offer_id": "SP-20261016-003", "customer_name": "Azersun MMC", "sample": True,
      "assumptions": ["x"], "lines": [], "pdf_base64": "JVBERg==",
      "totals": {"subtotal": "53592.00", "vat": "9646.56", "total": "63238.56", "currency": "AZN", "vat_included": False}}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("OFFER_AGENT_KEY", "k-user")
    monkeypatch.setenv("OFFER_AGENT_ADMIN_KEY", "k-admin")


def _mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- giriş yoxlaması -----------------------------------------------------------


def test_validate_ok_and_trims():
    body = agent.validate_offer_request({"brand": "nafa", "message": "  salam  ", "customer_name": " A ", "contact_person": ""})
    assert body == {"brand": "nafa", "include_pdf": True, "message": "salam", "customer_name": "A"}


@pytest.mark.parametrize("data", [
    None, {"brand": "x", "message": "a"}, {"brand": "seaport", "message": "   "},
    {"brand": "seaport", "message": "a" * 4001}, {"brand": "seaport", "message": 5},
    {"brand": "seaport", "messages": [{"role": "assistant", "content": "q"}]},
    {"brand": "seaport", "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "q"}]},
    {"brand": "seaport", "messages": [{"role": "bot", "content": "a"}]},
])
def test_validate_rejects(data):
    with pytest.raises(ValueError):
        agent.validate_offer_request(data)


def test_validate_conversation():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "q?"}, {"role": "user", "content": "b"}]
    body = agent.validate_offer_request({"brand": "seaport", "messages": msgs})
    assert body["messages"] == msgs and "message" not in body


# --- xəta xəritəsi və client -----------------------------------------------------


@pytest.mark.parametrize("status,out", [(401, 502), (403, 502), (404, 404), (409, 409), (422, 422), (502, 502), (500, 502)])
def test_error_mapping_hides_detail(status, out):
    def handler(request):
        return httpx.Response(status, json={"detail": "SECRET-INTERNAL traceback"})
    with pytest.raises(agent.OfferAgentError) as e:
        agent.create_offer({"brand": "seaport", "message": "a"}, client=_mock(handler))
    assert e.value.status == out and "SECRET" not in e.value.message


def test_timeout_and_connect_errors():
    def boom(request):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(agent.OfferAgentError) as e:
        agent.create_offer({}, client=_mock(boom))
    assert e.value.status == 504

    def down(request):
        raise httpx.ConnectError("no")
    with pytest.raises(agent.OfferAgentError) as e:
        agent.create_offer({}, client=_mock(down))
    assert e.value.status == 502


def test_missing_key_is_clear_error(monkeypatch):
    monkeypatch.delenv("OFFER_AGENT_KEY")
    with pytest.raises(agent.OfferAgentError) as e:
        agent.create_offer({}, client=_mock(lambda r: httpx.Response(200, json=OK)))
    assert e.value.status == 503


def test_uses_right_key_per_call():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers["authorization"]))
        return httpx.Response(200, json=OK if request.url.path == "/v1/offers" else [])
    c = _mock(handler)
    agent.create_offer({"brand": "seaport", "message": "a"}, client=c)
    agent.list_packages("seaport", client=c)
    agent.replace_packages("seaport", [{"id": "ab"}], client=c)
    agent.delete_package("seaport", "ab", client=c)
    assert [s[2] for s in seen] == ["Bearer k-user", "Bearer k-user", "Bearer k-admin", "Bearer k-admin"]


def test_clarification_passthrough():
    c = _mock(lambda r: httpx.Response(200, json={"status": "needs_clarification", "question": " Şirkət? "}))
    assert agent.create_offer({}, client=c) == {"status": "needs_clarification", "question": "Şirkət?"}


# --- route-lar (admin override) ----------------------------------------------------


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'o.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    web.app.dependency_overrides[offer_routes.verify_csrf_header] = lambda: None
    yield factory
    engine.dispose()


def test_create_offer_stores_metadata_and_never_leaks_key(app_db, monkeypatch):
    with app_db() as s:
        s.add(LeadRecord(dedupe_key="lk", source="oxu", source_url="u", title="T", raw_text="T", category="event",
                         scraped_at=__import__("datetime").datetime(2026, 10, 1)))
        s.commit()
    calls = []
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: calls.append(body) or dict(OK))
    c = TestClient(web.app)
    r = c.post("/api/offers", json={"brand": "seaport", "message": "salam", "lead_id": "lk"})
    assert r.status_code == 200 and r.json()["offer_id"] == "SP-20261016-003"
    assert "k-user" not in r.text and "lead_id" not in calls[0]
    with app_db() as s:
        rec = s.scalar(select(OfferRecord))
        assert (rec.offer_id, rec.brand, str(rec.total), rec.sample, rec.lead_id, rec.created_by_id) == (
            "SP-20261016-003", "seaport", "63238.56", True, "lk", 1)
    # təkrar cavab ikinci sətir yaratmır
    c.post("/api/offers", json={"brand": "seaport", "message": "salam"})
    with app_db() as s:
        assert len(list(s.scalars(select(OfferRecord)))) == 1


def test_create_offer_validation_and_agent_error(app_db, monkeypatch):
    c = TestClient(web.app)
    assert c.post("/api/offers", json={"brand": "seaport", "message": ""}).status_code == 422
    assert c.post("/api/offers", content=b"not json").status_code == 422

    def fail(body, client=None):
        raise agent.error_for_status(409)
    monkeypatch.setattr(agent, "create_offer", fail)
    r = c.post("/api/offers", json={"brand": "nafa", "message": "a"})
    assert r.status_code == 409 and "Paketlər" in r.json()["error"]


def test_pdf_only_for_known_offers(app_db, monkeypatch):
    monkeypatch.setattr(agent, "get_offer_pdf", lambda oid, client=None: b"%PDF-1.4")
    c = TestClient(web.app)
    assert c.get("/api/offers/SP-1-001/pdf").status_code == 404
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: dict(OK))
    c.post("/api/offers", json={"brand": "seaport", "message": "a"})
    r = c.get("/api/offers/SP-20261016-003/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content == b"%PDF-1.4"
    assert c.get("/api/offers/..%2Fx/pdf").status_code == 404


def test_pages_render_and_prefill(app_db, monkeypatch):
    import datetime
    with app_db() as s:
        s.add(LeadRecord(dedupe_key="lk", source="oxu", source_url="u", title="Banket sorğusu", raw_text="Banket sorğusu 100 nəfər",
                         entity_name="Azersun", category="event", scraped_at=datetime.datetime(2026, 10, 1)))
        s.commit()
    c = TestClient(web.app)
    html = c.get("/tools/offers?lead=lk").text
    assert "Qiymət təklifi" in html and "Lead-dən doldurulub" in html and '"lead_id": "lk"' in html
    assert offer_routes.lead_prefill(LeadRecord(dedupe_key="lk", title="T", raw_text="T body", entity_name="Azersun")) == {
        "lead_id": "lk", "message": "T\n\nT body", "customer_name": "Azersun"}
    assert "Alətlər" in html and "/tools/packages" in html
    assert c.get("/tools/packages").status_code == 200


def test_package_admin_routes_validate(app_db, monkeypatch):
    sent = {}
    monkeypatch.setattr(agent, "replace_packages", lambda b, p, client=None: sent.update(b=b, p=p) or {"count": len(p)})
    c = TestClient(web.app)
    assert c.put("/api/offer-packages/zzz", json={"packages": [{"id": "ab"}]}).status_code == 404
    assert c.put("/api/offer-packages/nafa", json={"packages": []}).status_code == 422
    assert c.put("/api/offer-packages/nafa", json={"packages": [{"id": "Bad Id"}]}).status_code == 422
    assert c.put("/api/offer-packages/nafa", json={"packages": [{"id": "ab"}, {"id": "ab"}]}).status_code == 422
    assert c.put("/api/offer-packages/nafa", json={"packages": [{"id": "ab"}]}).status_code == 200 and sent["b"] == "nafa"
    assert c.put("/api/offer-packages/nafa/ab", json={"id": "other"}).status_code == 422


# --- rollar (real giriş) -------------------------------------------------------------


@pytest.mark.real_auth
def test_roles_and_csrf(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    monkeypatch.setattr("app.auth.users.hash_password", lambda p: hash_password(p, iterations=1000))
    monkeypatch.setattr("app.auth.routes.hash_password", lambda p: hash_password(p, iterations=1000))
    security._failures.clear()
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: dict(OK))
    with factory() as s:
        for email, role in (("v@neo.az", "viewer"), ("s@neo.az", "sales"), ("a@neo.az", "admin")):
            create_user(s, email=email, full_name=email, role_code=role, password="möhkəm-şifrə-123")

    def login(email):
        c = TestClient(web.app)
        import re
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text).group(1)
        c.post("/login", data={"email": email, "password": "möhkəm-şifrə-123", "csrf_token": tok})
        page = c.get("/tools/offers")
        return c, page

    anon = TestClient(web.app)
    assert anon.post("/api/offers", json={"brand": "seaport", "message": "a"}).status_code == 401
    assert anon.get("/api/offers/SP-1-001/pdf").status_code == 401

    viewer, page = login("v@neo.az")
    assert page.status_code == 403
    assert viewer.post("/api/offers", json={"brand": "seaport", "message": "a"}).status_code == 403

    sales, page = login("s@neo.az")
    assert page.status_code == 200 and "Qiymət təklifi" in page.text and "/tools/packages" not in page.text
    assert sales.get("/tools/packages").status_code == 403
    assert sales.get("/api/offer-packages/seaport").status_code == 403
    assert sales.put("/api/offer-packages/seaport", json={"packages": [{"id": "ab"}]}).status_code == 403
    assert sales.delete("/api/offer-packages/seaport/ab").status_code == 403
    # CSRF başlığı olmadan təklif yaradıla bilməz
    assert sales.post("/api/offers", json={"brand": "seaport", "message": "a"}).status_code == 403
    import re
    token = re.search(r"var CSRF = \"([^\"]+)\"", page.text).group(1)
    ok = sales.post("/api/offers", json={"brand": "seaport", "message": "a"}, headers={"X-CSRF-Token": token})
    assert ok.status_code == 200

    admin, page = login("a@neo.az")
    assert page.status_code == 200 and "/tools/packages" in page.text
    assert admin.get("/tools/packages").status_code == 200
    engine.dispose()
