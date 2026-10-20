"""Validasiyada axtarış/LinkedIn linki, istifadəçinin öz açar sözləri, səsli izah, məktuba fayl,
rəhbərlərin LinkedIn profilləri (Apify saxtadır)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import brief, crm, keywords, leaders, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import Contact, LeadBrief, LeadRecord, Opportunity, Outreach, UserKeyword
from app.db.repository import LeadRepository
from app.models import Author, LeadCategory, RawLead
from app.outreach import mail

POST = "https://www.linkedin.com/posts/sahil-catering-7251234567890123456"


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'uf.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    with factory() as s:
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    yield factory
    engine.dispose()


def _lead(factory, url="https://sahil.az", *, title="Sahil MMC yeni zavod açır", raw="Zavodda 300 işçi çalışacaq.",
          source="boss", origin=None, entity="Sahil MMC", author=None):
    lead = RawLead(source=source, source_url=url, title=title, raw_text=raw, entity_name=entity,
                   category=LeadCategory.corporate, ai_score=0.8, status="pending_review",
                   origin_url=origin, author=author)
    key = lead.dedupe_key()
    with factory() as s:
        LeadRepository(s).save(key, lead)
        s.commit()
    return key


# --- 1, 2: Validasiyada axtarış və LinkedIn linki -----------------------------------


def test_validation_has_search_and_linkedin_title_opens_post(factory):
    _lead(factory, "https://vumes.az", source="linkedin", origin=POST, title="Vumes işçilərinə nahar verir")
    page = TestClient(web.app).get("/leads/pending_review").text
    assert 'id="ld-q"' in page
    assert f'<h3 class="lead-title"><a href="{POST}"' in page  # başlıq LinkedIn postuna aparır
    assert 'href="https://vumes.az"' in page and "Şirkət saytı" in page  # sayt ayrıca link


# --- 5: Hər istifadəçinin öz açar sözləri --------------------------------------------


def test_my_keywords_filter_validation(factory):
    _lead(factory, "https://a.az", title="Xəstəxana üçün yemək tenderi")
    _lead(factory, "https://b.az", title="Bankda yeni filial açıldı")
    client = TestClient(web.app)

    assert client.get("/me/keywords").status_code == 200
    r = client.post("/me/keywords", data={"terms": "xəstəxana\nXəstəxana", "match": "prefix"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    with factory() as s:
        assert [k.term for k in s.scalars(select(UserKeyword))] == ["xəstəxana"]  # dublikat atlanır

    page = client.get("/leads/pending_review?mine=1").text
    assert "Xəstəxana üçün yemək tenderi" in page and "Bankda yeni filial" not in page
    assert "Sənin sözün" in page
    assert "Bankda yeni filial" in client.get("/leads/pending_review").text  # filtrsiz hamısı


def test_cannot_delete_someone_elses_keyword(factory):
    with factory() as s:
        other = create_user(s, email="o@neo.az", full_name="Other", role_code="sales", password="y" * 12)
        s.add(UserKeyword(user_id=other.id, term="zavod", match="prefix"))
        s.commit()
        kw_id = s.scalar(select(UserKeyword.id))
    r = TestClient(web.app).post(f"/me/keywords/{kw_id}/delete", follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_matched_terms_folds_accents():
    terms = [keywords.Term("xəstəxana"), keywords.Term("zavod", "word")]
    assert keywords.matched_terms("Xestexanalar ucun", terms) == ["xəstəxana"]
    assert keywords.matched_terms("zavodlar", terms) == []  # "word" — tam söz


# --- 6: Səsli izah -------------------------------------------------------------------


def test_brief_is_created_once_and_served(factory, monkeypatch):
    key = _lead(factory)
    calls = []
    monkeypatch.setattr(brief, "write_script", lambda lead, client=None: calls.append("s") or "Sahil yeni zavod açır.")
    monkeypatch.setattr(brief, "speak", lambda script, client=None: (calls.append("a") or b"ID3mp3", "alloy"))
    client = TestClient(web.app)

    r = client.post(f"/leads/{key}/brief")
    assert r.status_code == 200 and r.json()["script"] == "Sahil yeni zavod açır."
    audio = client.get(r.json()["audio_url"])
    assert audio.status_code == 200 and audio.content == b"ID3mp3" and audio.headers["content-type"] == "audio/mpeg"

    client.post(f"/leads/{key}/brief")  # ikinci basış — AI yenidən çağırılmır
    assert calls == ["s", "a"]
    with factory() as s:
        assert s.get(LeadBrief, key).voice == "alloy"


def test_brief_without_openai_is_a_clear_error(factory):
    key = _lead(factory)
    r = TestClient(web.app).post(f"/leads/{key}/brief")
    assert r.status_code == 502 and "OPENAI_API_KEY" in r.json()["error"]


# --- 3: Məktub — təsdiqdən sonra fürsətə yönləndirmə, fayl əlavəsi ---------------------


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture()
def opp_id(factory):
    key = _lead(factory)
    with factory() as s:
        return crm.convert_lead(s, key, user_id=1).opportunity.id


def test_send_with_attachment(factory, opp_id, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setenv("SMTP_USER", "sales@dadli.az")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(mail.send_email, "__kwdefaults__", {"smtp_factory": FakeSMTP})
    form = {"to": "rauf@sahil.az", "subject": "Nahar təklifi", "body": "Hörmətli Rauf bəy, sizə təklifimiz var.",
            "language": "az", "send_key": "qaralama-acari-1234567890"}
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data=form,
                                 files=[("files", ("təklif.pdf", b"%PDF-1.4 test", "application/pdf"))],
                                 follow_redirects=False)
    assert "msg=" in r.headers["location"]
    parts = [p for p in FakeSMTP.sent[0].iter_attachments()]
    assert [(p.get_filename(), p.get_content_type(), p.get_content()) for p in parts] == [
        ("təklif.pdf", "application/pdf", b"%PDF-1.4 test")]
    with factory() as s:
        assert s.scalars(select(Outreach.attachments)).one() == "təklif.pdf"


def test_send_rejects_executable_and_keeps_draft(factory, opp_id):
    form = {"to": "rauf@sahil.az", "subject": "S", "body": "Kifayət qədər uzun məktub mətni.",
            "send_key": "qaralama-acari-1234567890"}
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data=form,
                                 files=[("files", ("setup.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 200 and "setup.exe" in r.text and "Kifayət qədər uzun məktub mətni." in r.text


def test_approve_without_page_reload_returns_json(factory):
    key = _lead(factory)
    client = TestClient(web.app)
    r = client.post(f"/leads/{key}/approve", headers={"Accept": "application/json"})
    with factory() as s:
        opp = s.scalars(select(Opportunity)).one()
    assert r.status_code == 200 and r.json()["opp_id"] == opp.id and r.json()["url"] == f"/opportunities/{opp.id}"
    again = client.post(f"/leads/{key}/approve", headers={"Accept": "application/json"})
    assert again.status_code == 409 and "error" in again.json()  # ikinci dəfə — aydın xəta, yönləndirmə yox
    mail_form = f'action="/opportunities/{opp.id}/email/draft#email"'
    assert mail_form in client.get(f"/opportunities/{opp.id}").text
    assert mail_form in client.get("/tasks?who=all").text


def test_mail_button_opens_draft_directly(factory, opp_id, monkeypatch):
    """Tapşırıqdakı "Məktub yaz": qaralama dərhal açılır, email-i olan ilk kontakt özü seçilir."""
    with factory() as s:
        opp = s.get(Opportunity, opp_id)
        s.add(Contact(lead_key=opp.lead_key, organization_id=opp.organization_id, name="Rauf", email="rauf@sahil.az",
                      source="ai"))
        s.commit()
    seen = {}

    def draft(**kw):
        seen.update(kw)
        return {"subject": "Nahar təklifi", "body": "Mətn " * 10}

    monkeypatch.setattr(mail, "draft_email", draft)
    page = TestClient(web.app).post(f"/opportunities/{opp_id}/email/draft", data={"auto": "1"}).text
    assert 'value="Nahar təklifi"' in page and 'value="rauf@sahil.az"' in page  # göndərmə formu açıqdır
    assert seen["contact_name"] == "Rauf" and seen["language"] == "az"


# --- Alətlər → Şirkət analizi ----------------------------------------------------------


def test_company_analysis_uses_db_facts(factory, monkeypatch):
    from app import company_analysis

    _lead(factory, title="Sahil MMC yeni zavod açır")
    seen = {}
    ai = {"summary": "Sahil logistika şirkətidir.", "industry": "Logistika", "size": "medium", "size_note": "bilinmir",
          "fit": "high", "fit_reason": "Yeni zavod.", "needs": ["işçi naharı"], "approach": ["HR-a yaz"],
          "talking_points": ["isti nahar"], "risks": []}

    def fake_chat(client, model, system, user, schema, name="out", max_tokens=700):
        seen["user"] = user
        return ai, None

    monkeypatch.setattr(company_analysis, "chat_json", fake_chat)
    monkeypatch.setattr(company_analysis, "_client", lambda c: None)
    client = TestClient(web.app)
    assert client.get("/tools/company").status_code == 200
    page = client.post("/tools/company", data={"name": "Sahil MMC"}).text
    assert "Sahil logistika şirkətidir." in page and "HR-a yaz" in page and "Yüksək" in page
    assert "Sahil MMC yeni zavod açır" in seen["user"]  # bazadakı lead AI-yə gedir


def test_company_analysis_without_openai_is_a_clear_error(factory):
    page = TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"}).text
    assert "OPENAI_API_KEY" in page


# --- 4: Rəhbərlərin LinkedIn profilləri ------------------------------------------------


def _apify(items, calls):
    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json=items)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_leaders_saved_as_contacts_once(factory, opp_id, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    calls = []
    items = [
        {"firstName": "Rauf", "lastName": "Əliyev", "headline": "Baş direktor — Sahil MMC",
         "linkedinUrl": "https://www.linkedin.com/in/rauf"},
        {"fullName": "Aysel Q.", "headline": "HR Manager", "linkedinUrl": "https://www.linkedin.com/in/aysel/"},
        {"fullName": "Kamran", "headline": "Sürücü", "linkedinUrl": "https://www.linkedin.com/in/kamran"},  # rəhbər deyil
        {"fullName": "Rauf dublikat", "headline": "CEO", "linkedinUrl": "https://www.linkedin.com/in/rauf/"},
    ]
    client = _apify(items, calls)
    monkeypatch.setattr(leaders, "_http", lambda: client)

    r = TestClient(web.app).post(f"/opportunities/{opp_id}/leaders")
    assert r.status_code == 200 and r.json() == {"added": 2}
    assert json.loads(calls[0].content)["companies"] == ["Sahil MMC"]
    with factory() as s:
        rows = s.scalars(select(Contact).where(Contact.source == "enrichment")).all()
        assert {c.name for c in rows} == {"Rauf Əliyev", "Aysel Q."}
        assert s.get(Opportunity, opp_id).leaders_fetched_at is not None

    again = TestClient(web.app).post(f"/opportunities/{opp_id}/leaders")  # avtomatik ikinci dəfə — Apify-a getmir
    assert again.json()["skipped"] is True and len(calls) == 1


def test_leaders_skip_linkedin_author_as_company(factory, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    key = _lead(factory, POST, source="linkedin", origin=POST, entity="Aysel Məmmədova",
                author=Author(name="Aysel Məmmədova", title="HR"))
    with factory() as s:
        assert leaders.company_name(s.get(LeadRecord, key), None) is None  # müəllifin adı şirkət deyil


def test_leaders_without_token_is_hidden(factory, opp_id):
    page = TestClient(web.app).get(f"/opportunities/{opp_id}").text
    assert "data-leaders" not in page
