"""AI məktub: qaralama (saxta OpenAI), göndərmə (saxta SMTP), tarixçə, tapşırıq, mərhələ, rollar."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import crm, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import Contact, LeadEvent, Opportunity, Outreach, Task
from app.db.repository import LeadRepository
from app.models import RawLead
from app.outreach import OutreachError, detect_language, mail


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'em.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    with factory() as s:
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    yield factory
    engine.dispose()


@pytest.fixture()
def opp_id(factory):
    lead = RawLead(source="boss", source_url="https://sahil.az", title="Sahil MMC işçilərinə nahar verir",
                   raw_text="Nahar şirkət tərəfindən təmin olunur. 120 işçi.", entity_name="Sahil MMC",
                   status="pending_review", ai_score=0.8)
    key = lead.dedupe_key()
    with factory() as s:
        LeadRepository(s).save(key, lead)
        opp = crm.convert_lead(s, key, user_id=1).opportunity
        s.add(Contact(lead_key=key, organization_id=opp.organization_id, name="Rauf Əliyev", title="Direktor",
                      email="rauf@sahil.az", source="ai"))
        s.commit()
        return opp.id


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.mark.parametrize("text, lang", [
    ("Şirkətimiz əməkdaşlar üçün nahar təşkil edir", "az"),
    ("Требуется организация питания сотрудников", "ru"),
    ("We are looking for a staff lunch supplier in Baku", "en"),
])
def test_detect_language(text, lang):
    assert detect_language(text) == lang


def test_draft_email_uses_language_and_returns_subject_body():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content = json.dumps({"subject": "Əməkdaşlarınız üçün nahar", "body": "Hörmətli Rauf bəy, ..."})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    out = mail.draft_email(lead_title="T", lead_text="mətn", org_name="Sahil MMC", contact_name="Rauf",
                           contact_title="Direktor", sender_name="Emil", language="ru", client=client)
    assert out == {"subject": "Əməkdaşlarınız üçün nahar", "body": "Hörmətli Rauf bəy, ..."}
    assert "Write in Russian" in calls[0]["messages"][0]["content"]
    assert "Sahil MMC" in calls[0]["messages"][1]["content"]


def test_draft_without_openai_key_is_a_clear_error():
    with pytest.raises(OutreachError, match="OPENAI_API_KEY"):
        mail.draft_email(lead_title="T", lead_text="", org_name=None, contact_name=None, contact_title=None,
                         sender_name="Emil", language="az")


def test_draft_route_prefills_form(factory, opp_id, monkeypatch):
    monkeypatch.setattr(mail, "draft_email", lambda **kw: {"subject": f"Salam {kw['contact_name']}", "body": "Mətn " * 10})
    monkeypatch.setenv("COMPANY_NAME", "Dadlı Catering")
    with factory() as s:
        contact_id = s.scalar(select(Contact.id))
    page = TestClient(web.app).post(f"/opportunities/{opp_id}/email/draft",
                                    data={"contact_id": str(contact_id), "language": "az"}).text
    assert 'value="Salam Rauf Əliyev"' in page
    assert 'value="rauf@sahil.az"' in page
    assert "Dadlı Catering" in page  # imza


def test_send_writes_history_task_and_moves_stage(factory, opp_id, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setenv("SMTP_USER", "sales@dadli.az")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.send_email, "__kwdefaults__", {"smtp_factory": FakeSMTP})

    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data={
        "to": "Rauf@Sahil.az", "subject": "Nahar təklifi", "body": "Hörmətli Rauf bəy, sizə təklifimiz var.",
        "language": "az"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]

    msg = FakeSMTP.sent[0]
    assert msg["To"] == "rauf@sahil.az" and msg["Reply-To"] == "test@neo.az"
    with factory() as s:
        out = s.scalars(select(Outreach)).one()
        opp = s.get(Opportunity, opp_id)
        tasks = [t.title for t in s.scalars(select(Task).where(Task.opportunity_id == opp_id))]
        events = [e.action for e in s.scalars(select(LeadEvent))]
    assert (out.status, out.kind, out.to_address, out.user_id) == ("sent", "email", "rauf@sahil.az", 1)
    assert opp.stage == "contacted"
    assert "Məktuba cavabı yoxla: rauf@sahil.az" in tasks
    assert events == ["approve", "email"]


def test_send_without_smtp_records_failure(factory, opp_id):
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data={
        "to": "rauf@sahil.az", "subject": "S", "body": "Kifayət qədər uzun məktub mətni."}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    with factory() as s:
        out = s.scalars(select(Outreach)).one()
        assert out.status == "failed" and "SMTP_USER" in out.error
        assert s.get(Opportunity, opp_id).stage == "new"


def test_send_rejects_bad_address(factory, opp_id):
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data={
        "to": "yox", "subject": "S", "body": "Kifayət qədər uzun məktub mətni."}, follow_redirects=False)
    assert "err=" in r.headers["location"]


@pytest.mark.real_auth
def test_viewer_cannot_send(factory, opp_id):
    with factory() as s:
        create_user(s, email="v@neo.az", full_name="Viewer", role_code="viewer", password="y" * 12)
    import re

    def csrf(path):
        return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text).group(1)

    client = TestClient(web.app)
    client.post("/login", data={"email": "v@neo.az", "password": "y" * 12, "next": "/", "csrf_token": csrf("/login")})
    page = client.get(f"/opportunities/{opp_id}").text
    assert "AI ilə məktub yaz" not in page
    token = csrf(f"/opportunities/{opp_id}")
    r = client.post(f"/opportunities/{opp_id}/email/send", data={
        "csrf_token": token, "to": "rauf@sahil.az", "subject": "S", "body": "Kifayət qədər uzun məktub mətni."})
    assert r.status_code == 403
