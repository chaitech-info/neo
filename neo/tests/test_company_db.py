"""Şirkət bazası: analiz saxlanılır (dublikatsız), şirkət kartı, əməkdaşlıq razılaşmaları,
analizdə işçilər (Apify saxtadır), fürsətin email ünvanları."""
import json
from datetime import date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import companies, company_analysis, crm, employees, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import ClientContract, Contact, EmployeeSearch, LeadRecord, Opportunity, Organization, Reminder
from app.db.repository import LeadRepository
from app.models import ContactInfo, LeadCategory, RawLead

AI = {"summary": "Sahil logistika şirkətidir.", "industry": "Logistika", "size": "medium", "size_note": "bilinmir",
      "fit": "high", "fit_reason": "Yeni zavod.", "needs": ["işçi naharı"], "approach": ["HR-a yaz"],
      "talking_points": ["isti nahar"], "risks": []}


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cdb.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    with factory() as s:
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    yield factory
    engine.dispose()


@pytest.fixture()
def fake_ai(monkeypatch):
    calls = []

    def fake_chat(client, model, system, user, schema, name="out", max_tokens=700):
        calls.append(user)
        return dict(AI), None

    monkeypatch.setattr(company_analysis, "chat_json", fake_chat)
    monkeypatch.setattr(company_analysis, "_client", lambda c: None)
    monkeypatch.setattr(company_analysis, "site_text", lambda url, crawler=None: ("", []))
    return calls


def _org_count(factory) -> int:
    with factory() as s:
        return s.scalar(select(func.count()).select_from(Organization))


# --- Analiz → şirkət bazası ---------------------------------------------------------------


def test_successful_analysis_is_saved_once(factory, fake_ai):
    client = TestClient(web.app)
    page = client.post("/tools/company", data={"name": "Sahil MMC", "website": "https://www.sahil.az/haqqimizda"}).text
    assert "şirkət bazasında saxlanıldı" in page
    with factory() as s:
        org = s.scalars(select(Organization)).one()
        assert org.domain == "sahil.az" and org.industry == "Logistika" and org.analysis_status == "ok"
        assert json.loads(org.analysis)["ai"]["summary"] == AI["summary"]
        first_at = org.analysed_at

    # Eyni şirkət başqa yazılışla (domen eyni) — yeni sətir yaranmır, analiz yenilənir.
    client.post("/tools/company", data={"name": "SAHIL", "website": "sahil.az"})
    assert _org_count(factory) == 1
    with factory() as s:
        assert s.scalars(select(Organization)).one().analysed_at >= first_at
    # Ad eyni, sayt verilməyib — yenə eyni şirkət.
    client.post("/tools/company", data={"name": "\"Sahil\" MMC"})
    assert _org_count(factory) == 1


def test_failed_analysis_is_not_saved(factory):
    page = TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"}).text  # OPENAI_API_KEY yoxdur
    assert "OPENAI_API_KEY" in page
    assert _org_count(factory) == 0


def test_company_database_list_search_and_card(factory, fake_ai, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    client = TestClient(web.app)
    client.post("/tools/company", data={"name": "Sahil MMC", "website": "sahil.az"})
    with factory() as s:
        companies.company_for(s, "Azersun Holding", "azersun.az")  # analizsiz və razılaşmasız — bazada görünmür
        s.commit()
        org_id = s.scalars(select(Organization.id).where(Organization.domain == "sahil.az")).one()

    listing = client.get("/companies").text
    assert "Sahil MMC" in listing and "Analiz olunub" in listing and "Azersun" not in listing
    assert "Sahil MMC" in client.get("/companies?q=logist").text  # sahə üzrə axtarış
    assert "Sahil MMC" not in client.get("/companies?q=azersun").text

    card = client.get(f"/companies/{org_id}").text
    assert AI["summary"] in card and "HR-a yaz" in card and "Əməkdaşlıq razılaşmaları" in card
    assert "İşçilər" in card and "APIFY_TOKEN" in card  # LinkedIn qoşulmayıb — açıq yazılır, uydurma yoxdur
    assert client.get("/companies/99999", follow_redirects=False).status_code == 303


# --- Razılaşmalar --------------------------------------------------------------------------


def test_add_company_with_several_agreements(factory):
    client = TestClient(web.app)
    resp = client.post("/tools/contracts", data={
        "company_name": "Azersun Holding MMC", "website": "azersun.az", "voen": "1234567890",
        "title": ["Nahar xidməti", "", "Korporativ tədbir"], "description": ["300 nəfər", "", ""],
        "starts_on": ["2026-01-01", "", "2026-12-20"], "ends_on": ["2027-01-01", "", ""],
        "status": ["active", "active", "draft"], "contact": ["", "", ""], "remind": "",
    }, follow_redirects=False)
    assert resp.status_code == 303 and "err=" not in resp.headers["location"]
    assert resp.headers["location"].startswith("/companies/")
    with factory() as s:
        org = s.scalars(select(Organization)).one()
        rows = s.scalars(select(ClientContract).order_by(ClientContract.id)).all()
        assert org.voen == "1234567890" and org.domain == "azersun.az"
        assert [(r.title, r.status, r.contract_end) for r in rows] == [
            ("Nahar xidməti", "active", date(2027, 1, 1)), ("Korporativ tədbir", "draft", None)]
        assert all(r.organization_id == org.id for r in rows)

    # Eyni VÖEN ilə yenidən — yeni şirkət yaranmır, razılaşma mövcud şirkətə əlavə olunur.
    client.post("/tools/contracts", data={"company_name": "Azersun", "voen": "1234567890",
                                          "title": ["Su təchizatı"], "status": ["active"]})
    assert _org_count(factory) == 1
    assert "Azersun Holding MMC" in client.get("/companies?show=agreements").text


def test_invalid_agreement_saves_nothing(factory):
    client = TestClient(web.app)
    resp = client.post("/tools/contracts", data={
        "company_name": "Azersun", "title": ["Nahar"], "starts_on": ["2027-02-01"], "ends_on": ["2027-01-01"],
        "status": ["active"]}, follow_redirects=False)
    assert "err=" in resp.headers["location"]
    bad_status = client.post("/tools/contracts", data={"company_name": "Azersun", "title": ["Nahar"],
                                                       "status": ["unknown"]}, follow_redirects=False)
    assert "err=" in bad_status.headers["location"]
    with factory() as s:
        assert not s.scalars(select(ClientContract)).all() and _org_count(factory) == 0


def test_agreement_manage_on_company_card(factory):
    client = TestClient(web.app)
    client.post("/tools/contracts", data={"company_name": "Sahil MMC", "title": [""], "status": ["active"]})
    with factory() as s:
        org_id = s.scalars(select(Organization.id)).one()
    assert "Sahil MMC" in client.get("/companies").text  # yalnız əlavə edilmiş şirkət də bazadadır

    client.post(f"/companies/{org_id}/agreements", data={"title": "Nahar xidməti", "status": "active",
                                                         "ends_on": "2027-05-01"})
    client.post(f"/companies/{org_id}/agreements", data={"title": "Tədbir", "status": "draft"})
    with factory() as s:
        rows = s.scalars(select(ClientContract).order_by(ClientContract.id)).all()
        assert len(rows) == 2
        aid = rows[0].id
        s.add(Reminder(user_id=1, title="x", due_at=datetime(2027, 4, 1), remind_before_min=0,
                       remind_at=datetime(2027, 4, 1), contract_id=rows[1].id))
        s.commit()

    resp = client.post(f"/agreements/{aid}", data={"title": "Nahar xidməti 2027", "status": "terminated",
                                                   "description": "erkən bitdi"}, follow_redirects=False)
    assert resp.headers["location"].startswith(f"/companies/{org_id}")
    with factory() as s:
        row = s.get(ClientContract, aid)
        assert (row.title, row.status, row.note, row.contract_end) == ("Nahar xidməti 2027", "terminated",
                                                                       "erkən bitdi", None)
        assert row.updated_at is not None
        other = s.scalars(select(ClientContract.id).where(ClientContract.id != aid)).one()

    client.post(f"/agreements/{other}/delete")
    with factory() as s:
        assert s.get(ClientContract, other) is None and not s.scalars(select(Reminder)).all()
    card = client.get(f"/companies/{org_id}").text
    assert "Nahar xidməti 2027" in card and "Ləğv edilib" in card


def test_shown_status_marks_past_active_as_expired():
    row = ClientContract(status="active", contract_end=date(2026, 1, 1))
    assert companies.shown_status(row, date(2026, 6, 1)) == "expired"
    assert companies.shown_status(ClientContract(status="draft"), date(2026, 6, 1)) == "draft"


def test_normalize_domain():
    assert companies.normalize_domain("https://WWW.Sahil.az/haqqimizda?x=1") == "sahil.az"
    assert companies.normalize_domain("sahil.az") == "sahil.az"
    assert companies.normalize_domain("sahil") is None and companies.normalize_domain("") is None


# --- Əlavə qeydlər ---------------------------------------------------------------------------


def test_notes_saved_on_analysis_and_card(factory, fake_ai):
    client = TestClient(web.app)
    page = client.post("/tools/company", data={"name": "Sahil MMC", "website": "sahil.az"}).text
    with factory() as s:
        org_id = s.scalars(select(Organization.id)).one()
    assert f'action="/companies/{org_id}/notes"' in page  # analiz səhifəsində qeyd forması

    r = client.post(f"/companies/{org_id}/notes", data={"notes": "  HR ilə görüş: noyabr  "},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] is True
    with factory() as s:
        org = s.get(Organization, org_id)
        assert org.notes == "HR ilə görüş: noyabr" and org.notes_updated_by_id == 1 and org.notes_updated_at

    card = client.get(f"/companies/{org_id}").text  # yenidən açılanda qeyd yerindədir
    assert "HR ilə görüş: noyabr" in card and "Əlavə qeydlər" in card
    assert "HR ilə görüş" in client.get("/companies?q=noyabr").text  # qeyddə axtarış

    # Yenidən analiz qeydi silmir.
    client.post("/tools/company", data={"name": "Sahil MMC", "website": "sahil.az"})
    with factory() as s:
        assert s.get(Organization, org_id).notes == "HR ilə görüş: noyabr"

    # JS-siz forma: yönləndirmə; boş mətn qeydi silir; çox uzun mətn rədd olunur.
    plain = client.post(f"/companies/{org_id}/notes", data={"notes": ""}, follow_redirects=False)
    assert plain.status_code == 303 and "msg=" in plain.headers["location"]
    with factory() as s:
        assert s.get(Organization, org_id).notes is None
    too_long = client.post(f"/companies/{org_id}/notes", data={"notes": "x" * 10001},
                           headers={"Accept": "application/json"})
    assert too_long.status_code == 400
    assert client.post("/companies/99999/notes", data={"notes": "x"},
                       headers={"Accept": "application/json"}).status_code == 404


def test_notes_from_add_company_form_are_appended(factory):
    client = TestClient(web.app)
    client.post("/tools/contracts", data={"company_name": "Sahil MMC", "notes": "Birinci qeyd", "title": [""]})
    client.post("/tools/contracts", data={"company_name": "Sahil MMC", "notes": "İkinci qeyd", "title": [""]})
    with factory() as s:
        assert s.scalars(select(Organization)).one().notes == "Birinci qeyd\n\nİkinci qeyd"


# --- Analizdə işçilər ----------------------------------------------------------------------


def _apify(items, calls):
    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json=items)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_employees_endpoint_returns_only_verified_profiles(factory, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    calls = []
    items = [
        {"fullName": "Rauf Əliyev", "headline": "Baş direktor", "linkedinUrl": "https://www.linkedin.com/in/rauf",
         "location": {"linkedinText": "Baku, Azerbaijan"}},
        {"fullName": "Adsız profil yoxdur"},  # LinkedIn linki yoxdur — göstərilmir
        {"headline": "HR", "linkedinUrl": "https://www.linkedin.com/in/noname"},  # adı yoxdur — göstərilmir
    ]
    monkeypatch.setattr(employees, "_http", lambda: _apify(items, calls))
    client = TestClient(web.app)
    r = client.post("/tools/company/employees", data={"company": "Sahil MMC"})
    assert r.status_code == 200
    data = r.json()
    assert [p["name"] for p in data["items"]] == ["Rauf Əliyev"] and data["items"][0]["title"] == "Baş direktor"
    assert data["from_cache"] is False

    again = client.post("/tools/company/employees", data={"company": "Sahil MMC"}).json()
    assert again["from_cache"] is True and len(calls) == 1  # keşdən, yenidən ödənilmir


def test_employees_endpoint_without_token_is_a_clear_error(factory, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    r = TestClient(web.app).post("/tools/company/employees", data={"company": "Sahil MMC"})
    assert r.status_code == 502 and "APIFY_TOKEN" in r.json()["error"]


def test_analysis_page_shows_cached_employees(factory, fake_ai):
    with factory() as s:
        s.add(EmployeeSearch(company="Sahil MMC", company_key=employees.company_key("Sahil MMC"), items=json.dumps(
            [{"name": "Aysel Q.", "title": "HR Manager", "linkedin_url": "https://www.linkedin.com/in/aysel"}])))
        s.commit()
    page = TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"}).text
    assert "Aysel Q." in page and "HR Manager" in page and "https://www.linkedin.com/in/aysel" in page


# --- Fürsətin email ünvanları -------------------------------------------------------------


def _opportunity(factory, email=None):
    lead = RawLead(source="boss", source_url="https://sahil.az/elan", title="Sahil MMC nahar axtarır", raw_text="mətn",
                   entity_name="Sahil MMC", category=LeadCategory.corporate, ai_score=0.7, status="pending_review",
                   contact_info=ContactInfo(email=email) if email else None)
    key = lead.dedupe_key()
    with factory() as s:
        LeadRepository(s).save(key, lead)
        s.commit()
        return crm.convert_lead(s, key, 1).opportunity.id


def test_opportunity_shows_all_emails_deduplicated(factory):
    opp_id = _opportunity(factory, email="Info@Sahil.az")
    with factory() as s:
        opp = s.get(Opportunity, opp_id)
        s.add(Contact(lead_key=opp.lead_key, organization_id=opp.organization_id, name="Rauf", title="HR",
                      email="hr@sahil.az", source="manual"))
        s.add(Contact(lead_key=opp.lead_key, email="info@sahil.az", source="text"))  # təkrar
        s.commit()
    client = TestClient(web.app)
    page = client.get(f"/opportunities/{opp_id}").text
    assert 'class="od-emails"' in page
    assert page.count('href="mailto:hr@sahil.az"') >= 1 and 'href="mailto:info@sahil.az"' in page
    assert page.count('data-copy="info@sahil.az"') == 1
    assert "✉ info@sahil.az" in client.get("/opportunities").text.lower()  # siyahıda şirkətin emaili


def test_opportunity_without_email_shows_no_empty_field(factory):
    opp_id = _opportunity(factory)
    client = TestClient(web.app)
    assert 'class="od-emails"' not in client.get(f"/opportunities/{opp_id}").text
    assert "✉" not in client.get("/opportunities").text


def test_letter_can_be_addressed_to_org_email(factory, monkeypatch):
    from app.outreach import mail

    opp_id = _opportunity(factory, email="info@sahil.az")
    monkeypatch.setattr(mail, "draft_email", lambda **kw: {"subject": "Nahar təklifi", "body": "Salam, " + "x" * 40})
    page = TestClient(web.app).post(f"/opportunities/{opp_id}/email/draft",
                                    data={"contact_id": "to:info@sahil.az", "language": "az"}).text
    assert 'name="to" required value="info@sahil.az"' in page
    # Fürsətə aid olmayan ünvan "to:" ilə seçilə bilməz.
    other = TestClient(web.app).post(f"/opportunities/{opp_id}/email/draft",
                                     data={"contact_id": "to:x@evil.az", "language": "az"}).text
    assert 'name="to" required value=""' in other
    with factory() as s:
        assert s.get(LeadRecord, s.get(Opportunity, opp_id).lead_key).contact_email == "info@sahil.az"
