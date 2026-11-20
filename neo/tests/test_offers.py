"""Qiymət təklifi aləti: giriş yoxlaması, agent xəta xəritəsi, proxy route-ları, rol məhdudiyyəti."""
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import offer_client as agent
from app import offer_pdf
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


WEEK = {
    "Bazar ertəsi": ["Şorba: Mərcimək şorbası", "Əsas yemək: Çoban qovurması", "Əsas yemək: Toyuq tabaka",
                     "Qarnir: Südlü kartof püresi", "Qarnir: Qril tərəvəzlər", "Salatlar: Kələm və kök salatı",
                     "Salatlar: Pomidor-xiyar salatı", "Çörək: Çörək assortisi"],
    "Çərşənbə axşamı": ["Şorba: Tərəvəz şorbası", "Əsas yemək: Pomidorla təqdim olunan qril kotlet",
                        "Əsas yemək: Toyuq şnitseli", "Qarnir: Sobada bişmiş kartof", "Qarnir: Qarabaşaq sıyığı",
                        "Salatlar: Çuğundur salatı", "Salatlar: Paytaxt salatı", "Çörək: Çörək assortisi"],
    "Çərşənbə": ["Şorba: Düyü şorbası", "Əsas yemək: Mal əti ilə tərəvəz sotesi", "Əsas yemək: Pendirli burger",
                 "Qarnir: Şüyüdlü plov", "Qarnir: Qril tərəvəzlər", "Salatlar: Vineqret salatı",
                 "Salatlar: Kələm və kök salatı", "Çörək: Çörək assortisi"],
    "Cümə axşamı": ["Şorba: Yarım noxud şorbası", "Əsas yemək: Adana lülə kababı", "Əsas yemək: Toyuq kordon-blyu",
                    "Qarnir: Kartof püresi", "Qarnir: Sobada bişmiş kartof", "Salatlar: Pomidor-xiyar salatı",
                    "Salatlar: Çuğundur salatı", "Çörək: Çörək assortisi"],
    "Cümə": ["Şorba: Göbələk krem-şorbası", "Əsas yemək: Yarpaq dolması", "Əsas yemək: Langet",
             "Qarnir: Qarabaşaq sıyığı", "Qarnir: Qril tərəvəzlər", "Salatlar: Paytaxt salatı",
             "Salatlar: Vineqret salatı", "Çörək: Çörək assortisi"],
}


def _snapshot(menu, price="6.00"):
    return {"id": "SP-20261016-003", "brand": "seaport", "language": "az", "customer_name": "Azersun",
            "lines": [{"package_id": "standart", "name": {"az": "Standart", "en": "Standard"}, "menu": menu,
                       "unit": {"az": "porsiya"}, "unit_price": price}],
            "totals": {"total": None}, "assumptions": [], "example": False}


WEEKLY = _snapshot([{"title": {"az": d}, "items": [{"az": i} for i in items]} for d, items in WEEK.items()])
FLAT = _snapshot([{"title": {"az": "Şorba"}, "items": [{"az": "Mərci şorbası"}]},
                  {"title": {"az": "Əsas yemək"}, "items": [{"az": "Toyuq əti"}, {"az": "Düyü"}]},
                  {"title": {"az": "Digər", "en": "Other"}, "items": [{"az": "Salat"}, {"az": "Çörək"}]}], "4.90")


def _pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", pdf))


def test_weekly_menu_pdf_matches_the_three_page_design():
    """Nümunə: SeaPort_Catering_Standard_Lunch_Menu_6AZN.pdf — üz qabığı + 2 menyu səhifəsi."""
    doc = offer_pdf.Doc(WEEKLY)
    pkg = doc.packages[0]
    assert pkg.weekly and [b.accent for b in pkg.blocks] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert [g.label for g in pkg.blocks[0].groups] == ["Şorba", "Əsas yemək", "Qarnir", "Salatlar", "Çörək"]
    assert offer_pdf.daily_selection(pkg) == [(1, "şorba"), (2, "əsas yemək"), (2, "qarnir"), (2, "salat"),
                                              (None, "Çörək assortisi")]
    assert offer_pdf.summary_line(pkg) == "Hər gün 1 şorba, 2 əsas yemək, 2 qarnir, 2 salat, çörək assortisi."
    assert pkg.price == "6 AZN" and doc.title() == "Standard Corporate Lunch Menu"
    pdf = offer_pdf.render(WEEKLY)
    assert pdf.startswith(b"%PDF") and _pages(pdf) == 3
    assert b"NotoSerif" in pdf and b"Caladea" not in pdf  # bütün mətn bir şriftdə — ə sözün içində fərqlənmir


def test_illustrated_pdf_goes_cover_then_photos_without_the_weekly_menu(monkeypatch):
    """Şəkilli təklif: üz qabığından sonra yemək şəkilləri, həftəlik menyu/gün blokları yoxdur, təklif
    məlumatları sonda. Şəkilsiz təklifdə həftəlik menyu var."""
    import io

    from PIL import Image

    from app.dishes import dish_key

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "JPEG")
    calls = []
    for name in ("day_block", "gallery", "proposal"):
        original = getattr(offer_pdf.Doc, name)
        monkeypatch.setattr(offer_pdf.Doc, name,
                            (lambda n, o: lambda self, *a, **k: (calls.append(n), o(self, *a, **k))[1])(name, original))

    offer_pdf.render(WEEKLY)
    assert "day_block" in calls and "gallery" not in calls
    calls.clear()
    pdf = offer_pdf.render(WEEKLY, {dish_key("Mərcimək şorbası"): buf.getvalue()})
    assert pdf.startswith(b"%PDF") and "day_block" not in calls
    assert calls[:2] == ["gallery", "proposal"]  # şəkillər, sonra təklif məlumatları
    assert _pages(pdf) == 2  # üz qabığı + şəkillər/təklif məlumatları

    texts = []
    original_draw = offer_pdf.Text.draw
    monkeypatch.setattr(offer_pdf.Text, "draw", lambda self, x, y, s, *a, **k: (texts.append(s), original_draw(self, x, y, s, *a, **k))[1])
    offer_pdf.render(WEEKLY, {dish_key("Mərcimək şorbası"): buf.getvalue()})
    drawn = " | ".join(texts)
    for word in ("Bazar ertəsi", "Monday", "Weekly", "Həftəlik", "Hər gün"):
        assert word not in drawn, word  # üz qabığında da günlər yazılmır


def test_flat_menu_pdf_and_text_fallback():
    doc = offer_pdf.Doc(FLAT)
    pkg = doc.packages[0]
    assert not pkg.weekly and len(pkg.blocks) == 1 and pkg.price == "4.90 AZN"
    assert offer_pdf.daily_selection(pkg) == [(1, "şorba"), (2, "əsas yemək"), (None, "Salat"), (None, "Çörək")]
    assert _pages(offer_pdf.render(FLAT)) == 2
    assert _pages(offer_pdf.render({"brand": "nafa", "lines": []})) == 2  # boş təklif də sınmır


def test_pdf_only_for_known_offers(app_db, monkeypatch):
    monkeypatch.setattr(agent, "get_offer", lambda oid, client=None: WEEKLY)
    monkeypatch.setattr(agent, "get_offer_pdf", lambda oid, client=None: b"%PDF-1.4 agent")
    c = TestClient(web.app)
    assert c.get("/api/offers/SP-1-001/pdf").status_code == 404
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: dict(OK))
    c.post("/api/offers", json={"brand": "seaport", "message": "a"})
    r = c.get("/api/offers/SP-20261016-003/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and _pages(r.content) == 3  # Neo-nun öz dizaynı
    assert c.get("/api/offers/..%2Fx/pdf").status_code == 404

    def broken(snapshot):
        raise ValueError("bad snapshot")
    monkeypatch.setattr(offer_pdf, "render", broken)
    assert c.get("/api/offers/SP-20261016-003/pdf").content == b"%PDF-1.4 agent"  # ehtiyat: agentin PDF-i


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
    assert "Təklif və menyu" in html and "/tools/packages" in html  # menyu bölməsi (base.html)
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


def test_created_offer_preview_uses_neo_design(app_db, monkeypatch):
    """Yaradanda önizləmə/yükləmə (pdf_base64) də Neo-nun PDF-idir, agentinki yox."""
    import base64
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: {**OK, **WEEKLY, "status": "ok",
                                                                          "offer_id": OK["offer_id"]})
    d = TestClient(web.app).post("/api/offers", json={"brand": "seaport", "message": "a"}).json()
    pdf = base64.b64decode(d["pdf_base64"])
    assert pdf.startswith(b"%PDF") and _pages(pdf) == 3 and b"NotoSerif" in pdf


def test_created_offer_preview_uses_the_full_offer_when_lines_have_no_menu(app_db, monkeypatch):
    """Agentin "yarat" cavabında sətir var, menyu yoxdur — sağdakı önizləmə tam təklifdən qurulmalıdır
    (əvvəl boş "Menyu" səhifəsi çıxırdı, düzgün PDF yalnız tarixçədə idi)."""
    import base64

    short = {**OK, "lines": [{"package_id": "standart", "name": "Standart", "unit_price": "8.00"}]}
    fetched = []
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: dict(short))
    monkeypatch.setattr(agent, "get_offer", lambda oid, client=None: fetched.append(oid) or WEEKLY)
    drawn = []
    original = offer_pdf.Doc.day_block
    monkeypatch.setattr(offer_pdf.Doc, "day_block", lambda self, b: (drawn.append(b), original(self, b))[1])
    r = TestClient(web.app).post("/api/offers", json={"brand": "seaport", "message": "a", "package_id": "standart"})
    assert r.status_code == 200 and fetched == ["SP-20261016-003"]
    pdf = base64.b64decode(r.json()["pdf_base64"])
    assert _pages(pdf) == 3 and drawn  # həftəlik menyu çəkilib


# --- dizaynlar, qiymət şərti, paketin təsviri -------------------------------------------


def _texts(monkeypatch, fn):
    texts = []
    original = offer_pdf.Text.draw
    monkeypatch.setattr(offer_pdf.Text, "draw", lambda self, x, y, s, *a, **k: (texts.append(s), original(self, x, y, s, *a, **k))[1])
    fn()
    monkeypatch.setattr(offer_pdf.Text, "draw", original)
    return " | ".join(texts)


def test_five_designs_render_the_same_pages_in_their_own_look():
    assert list(offer_pdf.DESIGNS) == ["classic", "midnight", "garden", "terra", "mono"]
    assert offer_pdf.design_code("zzz") == offer_pdf.design_code(None) == "classic"
    sizes = set()
    for code, d in offer_pdf.DESIGNS.items():
        pdf = offer_pdf.render(WEEKLY, design=code)
        assert pdf.startswith(b"%PDF") and _pages(pdf) == 3, code   # dizayn səhifə düzümünü dəyişmir
        assert (b"NotoSans" in pdf) == d.sans and (b"NotoSerif" in pdf) != d.sans, code
        sizes.add(len(pdf))
    assert len(sizes) == 5                                          # beşi də fərqli PDF-dir
    assert _pages(offer_pdf.render(WEEKLY, design="yox")) == 3      # tanınmayan kod sınmır — klassik


def test_pdf_states_price_is_per_person_and_vat_is_not_included(monkeypatch):
    drawn = _texts(monkeypatch, lambda: offer_pdf.render(WEEKLY))
    assert "1 nəfərlik" in drawn and "Qiymətə ƏDV daxil deyil." in drawn and "1 nəfər" in drawn.split(" | ")
    assert "VAT is not included." in drawn

    with_totals = dict(WEEKLY, totals=dict(OK["totals"]))           # ƏDV xaric 53 592, ƏDV ilə 63 238.56
    drawn = _texts(monkeypatch, lambda: offer_pdf.render(with_totals))
    assert "53 592 AZN" in drawn and "ƏDV daxil deyil" in drawn     # cəmi məbləğ ƏDV-siz göstərilir
    assert "63 238.56" in drawn                                     # ƏDV ilə məbləğ qeyddədir

    included = dict(WEEKLY, totals=dict(total="100.00", currency="AZN", vat_included=True))
    drawn = _texts(monkeypatch, lambda: offer_pdf.render(included))
    assert "daxildir" in drawn and "daxil deyil" not in drawn       # agent ƏDV daxil deyibsə — yalan yazılmır


def test_pdf_cover_uses_the_package_description(app_db, monkeypatch):
    """Agentin təklifindəki sətirdə təsvir yoxdursa, paketin öz təsviri götürülür (əvvəl ümumi mətn yazılırdı)."""
    described = dict(az="Ofis üçün isti nahar.")
    monkeypatch.setattr(agent, "list_packages", lambda brand, client=None: dict(packages=[
        dict(id="standart", description=described), dict(id="other", description="x")]))
    filled = offer_routes.with_descriptions(WEEKLY)
    assert filled["lines"][0]["description"] == described and "description" not in WEEKLY["lines"][0]
    drawn = _texts(monkeypatch, lambda: offer_routes.render_pdf(WEEKLY))
    assert "Ofis üçün isti nahar." in drawn and "Sevilən klassik" not in drawn

    own = dict(WEEKLY, lines=[dict(WEEKLY["lines"][0], description="Təklifin öz təsviri")])
    assert offer_routes.with_descriptions(own) is own               # təklifdə təsvir varsa — toxunulmur

    def down(brand, client=None):
        raise agent.OfferAgentError("yox", 502)
    monkeypatch.setattr(agent, "list_packages", down)
    assert offer_routes.render_pdf(WEEKLY).startswith(b"%PDF")      # agent cavab verməsə PDF yenə qurulur


def test_design_is_chosen_stored_and_can_be_changed(app_db, monkeypatch):
    import base64

    created = dict(OK, **WEEKLY, status="ok", offer_id=OK["offer_id"])
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: dict(created))
    monkeypatch.setattr(agent, "get_offer", lambda oid, client=None: WEEKLY)
    c = TestClient(web.app)
    page = c.get("/tools/offers").text
    for d in offer_pdf.DESIGNS.values():
        assert f'name="design" value="{d.code}"' in page and d.name in page
    r = c.post("/api/offers", json=dict(brand="seaport", message="a", design="garden"))
    assert b"NotoSans" in base64.b64decode(r.json()["pdf_base64"])
    with app_db() as s:
        assert s.scalar(select(OfferRecord.design)) == "garden"
    oid = OK["offer_id"]
    assert b"NotoSans" in c.get(f"/api/offers/{oid}/pdf").content                  # tarixçədən — saxlanmış dizayn
    assert b"NotoSerif" in c.get(f"/api/offers/{oid}/pdf?design=terra").content    # başqa dizaynda açmaq
    assert c.post(f"/api/offers/{oid}/design", json=dict(design="zzz")).status_code == 422
    assert c.post("/api/offers/SP-0-000/design", json=dict(design="mono")).status_code == 404
    assert c.post(f"/api/offers/{oid}/design", json=dict(design="midnight")).json() == dict(ok=True, design="midnight")
    with app_db() as s:
        assert s.scalar(select(OfferRecord.design)) == "midnight"
        s.query(OfferRecord).delete()
        s.commit()
    c.post("/api/offers", json=dict(brand="seaport", message="a", design="<script>"))  # tanınmayan dizayn — klassik
    with app_db() as s:
        assert s.scalar(select(OfferRecord.design)) == "classic"


# --- sorğuda yazılmış qiymət ---------------------------------------------------------------


@pytest.mark.parametrize("text, price", [
    ("STP ucun qiymeti 1 nefer ucun 8 yox 5 azn et", "5.00"),
    ("Qiyməti 6,50 manat olsun, 120 nəfər", "6.50"),
    ("8 azn deyil 7 azn hesabla", "7.00"),
    ("qiymət 5 azn et. Sonra: qiyməti 4 azn et", "4.00"),          # sonuncu göstəriş
    ("Azersun MMC, gündə 120 nəfər, 22 gün", None),                # qiymət yoxdur
    ("büdcə 3000 azn, 120 nəfər", None),                           # ümumi büdcə vahid qiymət deyil
    ("qiymət neçədir?", None), ("", None), ("qiyməti 0 azn et", None), ("qiyməti 5000 azn et", None),
])
def test_requested_price_by_rule(text, price):
    from decimal import Decimal

    from app import offer_price

    assert offer_price.requested_price(text) == (Decimal(price) if price else None)


def test_requested_price_prefers_the_ai_answer(monkeypatch):
    from decimal import Decimal

    from app import offer_price

    asked = []
    answer = dict(unit_price=9, remove_categories=["İçki", "Bilinməyən"], remove_dishes=["Lobya  şorbası", "x"])
    monkeypatch.setattr(offer_price, "_by_ai", lambda text: (asked.append(text), (True, dict(answer)))[1])
    changes = offer_price.requested_changes("bir nəfərə doqquz manat olsun, 9 azn, ickini cixart")
    assert changes.price == Decimal("9.00") and changes.categories == ("İçki",) and changes.dishes == ("Lobya şorbası",)
    assert changes.exclude == dict(categories=["İçki"], dishes=["Lobya şorbası"])
    empty = dict(unit_price=None, remove_categories=[], remove_dishes=[])
    monkeypatch.setattr(offer_price, "_by_ai", lambda text: (True, dict(empty)))   # AI: heç nə istənməyib
    assert offer_price.requested_price("qiyməti 8 yox 5 azn et") is None
    assert not offer_price.requested_changes("içkini çıxart") and offer_price.requested_changes("içkini çıxart").exclude is None
    asked.clear()
    assert offer_price.requested_price("Azersun, 120 nəfər") is None and not asked   # göstəriş yoxdur — AI çağırılmır


@pytest.mark.parametrize("text, categories", [
    ("SGTP ucun qiymeti 1 nefer ucun 8 yox 5 azn et\nmenudan ickini cixart", ("İçki",)),
    ("Menyudan içkiləri çıxar, desert də olmasın", ("Desert", "İçki")),
    ("çörək lazım deyil", ("Çörək",)), ("без напитков, 100 человек", ("İçki",)),
    ("şorbanı sil", ("Şorba",)),
    ("içki də olsun, 120 nəfər", ()), ("Azersun, gündə 120 nəfər", ()), ("desert əlavə et", ()),
])
def test_removed_categories_by_rule(text, categories):
    from app import offer_price

    assert offer_price.requested_changes(text).categories == categories


def test_load_exclude_is_strict():
    from app import offer_price

    assert offer_price.load_exclude(None) is None and offer_price.load_exclude("not json") is None
    assert offer_price.load_exclude('{"categories": ["Yox"], "dishes": []}') is None
    stored = offer_price.dump_exclude(dict(categories=["İçki"], dishes=["Langet"]))
    assert offer_price.load_exclude(stored) == dict(categories=["İçki"], dishes=["Langet"])
    assert offer_price.dump_exclude(None) is None


DRINKS = {d: items + ["İçki: Alma kompotu", "İçki: Limonad", "İçki: Qazsız su"] for d, items in WEEK.items()}
WITH_DRINKS = _snapshot([{"title": {"az": d}, "items": [{"az": i} for i in items]} for d, items in DRINKS.items()], "8.00")


def test_removed_category_and_dish_leave_the_pdf(monkeypatch):
    doc = offer_pdf.Doc(WITH_DRINKS)
    assert "3 içki" in offer_pdf.summary_line(doc.packages[0])
    drawn = _texts(monkeypatch, lambda: offer_pdf.render(WITH_DRINKS))
    assert "Limonad" in drawn and "İçki" in drawn

    exclude = dict(categories=["İçki"], dishes=["langet"])
    doc = offer_pdf.Doc(WITH_DRINKS, exclude=exclude)
    pkg = doc.packages[0]
    assert all(g.label != "İçki" for b in pkg.blocks for g in b.groups) and len(pkg.blocks) == 5
    assert "içki" not in offer_pdf.summary_line(pkg) and "2 əsas yemək" in offer_pdf.summary_line(pkg)
    drawn = _texts(monkeypatch, lambda: offer_pdf.render(WITH_DRINKS, exclude=exclude))
    for gone in ("Limonad", "Alma kompotu", "Qazsız su", "İçki", "içki", "Langet"):
        assert gone not in drawn, gone
    assert "Yarpaq dolması" in drawn and "Çörək assortisi" in drawn                 # qalanlar yerindədir
    assert offer_pdf.without(pkg, None) is pkg


def test_removal_written_in_the_request_is_applied_and_stored(app_db, monkeypatch):
    """"menudan ickini cixart": agent menyunu içki ilə qaytarır — PDF (yaradanda və tarixçədən) içkisiz olmalıdır."""
    offer = dict(WITH_DRINKS, totals=dict(subtotal="8.00", vat="1.44", total="9.44", currency="AZN", vat_included=False))
    created = dict(OK, **offer, status="ok", offer_id=OK["offer_id"])
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: dict(created))
    monkeypatch.setattr(agent, "get_offer", lambda oid, client=None: offer)
    c = TestClient(web.app)
    made = {}
    drawn = _texts(monkeypatch, lambda: made.update(c.post("/api/offers", json=dict(
        brand="seaport", message="SGTP ucun qiymeti 1 nefer ucun 8 yox 5 azn et\nmenudan ickini cixart",
        package_id="standart")).json()))
    assert "Limonad" not in drawn and "5 AZN" in drawn and "Çoban qovurması" in drawn
    assert any("menyudan çıxarıldı: İçki" in a for a in made["assumptions"])
    assert made["lines"][0]["unit_price"] == "5.00"
    with app_db() as s:
        rec = s.scalar(select(OfferRecord))
        assert rec.menu_exclude and "İçki" in rec.menu_exclude
    drawn = _texts(monkeypatch, lambda: c.get(f"/api/offers/{OK['offer_id']}/pdf"))   # tarixçədən — yenə içkisiz
    assert "Limonad" not in drawn and "5 AZN" in drawn


def test_apply_price_scales_the_line_and_the_totals():
    from decimal import Decimal

    from app import offer_price

    snap = {"lines": [{"package_id": "standart", "unit_price": "8.00", "quantity": 100, "days": 22, "line_total": "17600.00"}],
            "totals": {"subtotal": "17600.00", "vat": "3168.00", "total": "20768.00", "currency": "AZN", "vat_included": False}}
    out = offer_price.apply_price(snap, Decimal("5.00"))
    assert out["lines"][0]["unit_price"] == "5.00" and out["lines"][0]["line_total"] == "11000.00"
    assert out["totals"] == {"subtotal": "11000.00", "vat": "1980.00", "total": "12980.00", "currency": "AZN", "vat_included": False}
    assert snap["lines"][0]["unit_price"] == "8.00"                                  # əsli dəyişmir

    # Agent "8 yox 5 azn"dakı 5-i miqdar kimi oxuyub 8 × 5 = 40 qaytarır, miqdar/gün isə boşdur (prod,
    # SP-20261120-010: 25 / 4,50 / 29,50 çıxırdı). Miqdar bilinmirsə yekun 1 nəfərlik qiymətin özüdür.
    odd = {"lines": [{"package_id": "standart", "unit_price": "8.00", "quantity": None, "days": None, "line_total": "40.00"}],
           "totals": {"subtotal": "40.00", "vat": "7.20", "total": "47.20", "currency": "AZN", "vat_included": False}}
    out = offer_price.apply_price(odd, Decimal("5.00"))
    assert out["lines"][0]["line_total"] == "5.00"
    assert (out["totals"]["subtotal"], out["totals"]["vat"], out["totals"]["total"]) == ("5.00", "0.90", "5.90")
    inclusive = {"lines": [{"unit_price": "8", "quantity": 10, "days": 2, "line_total": "160"}],
                 "totals": {"total": "160", "currency": "AZN", "vat_included": True}}
    assert offer_price.apply_price(inclusive, Decimal("5"))["totals"]["total"] == "100.00"   # 5 × 10 × 2
    assert offer_price.apply_price(snap, None) is snap
    two = {"lines": [{"unit_price": "8"}, {"unit_price": "10"}]}
    assert offer_price.apply_price(two, Decimal("5")) is two                          # hansı paketə aiddir — bilinmir


def test_price_written_in_the_request_is_used_in_the_offer_and_the_pdf(app_db, monkeypatch):
    """"8 yox 5 azn et": agent paketin qiymətini (8) qaytarır — təklif, PDF və tarixçə 5 ilə olmalıdır."""
    import base64
    from decimal import Decimal

    line = dict(WEEKLY["lines"][0], unit_price="8.00", quantity=1, days=1, line_total="8.00")
    offer = dict(WEEKLY, lines=[line], totals=dict(subtotal="8.00", vat="1.44", total="9.44", currency="AZN", vat_included=False))
    created = dict(OK, **offer, status="ok", offer_id=OK["offer_id"])
    sent = []
    monkeypatch.setattr(agent, "create_offer", lambda body, client=None: sent.append(body) or dict(created))
    monkeypatch.setattr(agent, "get_offer", lambda oid, client=None: offer)
    c = TestClient(web.app)

    def pdf_text(render):
        return _texts(monkeypatch, render)

    made = {}
    drawn = pdf_text(lambda: made.update(c.post("/api/offers", json=dict(
        brand="seaport", message="STP ucun qiymeti 1 nefer ucun 8 yox 5 azn et", package_id="standart")).json()))
    assert made["lines"][0]["unit_price"] == "5.00" and made["totals"]["subtotal"] == "5.00"
    assert made["totals"]["total"] == "5.90" and any("5 AZN" in a and "8.00" in a for a in made["assumptions"])
    assert "5 AZN" in drawn and "8 AZN" not in drawn and "5.90 AZN" in drawn
    assert base64.b64decode(made["pdf_base64"]).startswith(b"%PDF")
    with app_db() as s:
        rec = s.scalar(select(OfferRecord))
        assert rec.unit_price_override == Decimal("5.00") and rec.total == Decimal("5.90")

    oid = OK["offer_id"]
    drawn = pdf_text(lambda: c.get(f"/api/offers/{oid}/pdf"))                         # tarixçədən — yenə 5
    assert "5 AZN" in drawn and "8 AZN" not in drawn
    assert c.get(f"/api/offers/{oid}").json()["lines"][0]["unit_price"] == "5.00"

    # Qiymət yazılmayan sorğu — paketin qiyməti qalır
    with app_db() as s:
        s.query(OfferRecord).delete()
        s.commit()
    plain = c.post("/api/offers", json=dict(brand="seaport", message="Azersun, 1 nəfər", package_id="standart")).json()
    assert plain["lines"][0]["unit_price"] == "8.00" and plain["assumptions"] == []
    with app_db() as s:
        assert s.scalar(select(OfferRecord.unit_price_override)) is None
