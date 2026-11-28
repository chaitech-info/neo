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


SEND_KEY = "qaralama-acari-1234567890"


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


@pytest.mark.parametrize("name, expected", [
    ("Rauf Əliyev", "Hörmətli Rauf bəy,"),
    ("Əliyeva Aygün", "Hörmətli Aygün xanım,"),
    ("Məmmədov Anar Rəşid oğlu", "Hörmətli Anar bəy,"),
    ("Leyla Kamran qızı", "Hörmətli Leyla xanım,"),
    ("Əli Həsənzadə", "Hörmətli Əli bəy / xanım,"),          # cinsi bilinmir — istifadəçi seçir
    ("", "Hörmətli cənab / xanım,"),
    (None, "Hörmətli cənab / xanım,"),
])
def test_builtin_template_salutation(name, expected):
    from app.outreach import letters

    assert letters.salutation(name) == expected


def test_builtin_template_is_filled_without_ai_and_signed_as_the_template(factory, opp_id, monkeypatch):
    """Hazır Seaport şablonu: mətn olduğu kimi, müraciət kontaktın adından, imza şablonunku; AI çağırılmır."""
    from app.outreach import letters

    def no_ai(**kw):
        raise AssertionError("hazır şablon AI-ya getməməlidir")

    monkeypatch.setattr(mail, "draft_email", no_ai)
    with factory() as s:
        contact_id = s.scalar(select(Contact.id))
    client = TestClient(web.app)
    form_page = client.get(f"/opportunities/{opp_id}").text
    assert f'value="{letters.DEFAULT}" selected' in form_page                 # formda ilk seçim
    page = client.post(f"/opportunities/{opp_id}/email/draft",
                       data={"contact_id": str(contact_id), "language": "az", "template_id": letters.DEFAULT}).text
    for text in ("Hörmətli Rauf bəy,", "Sizi “Seaport Catering” şirkəti adından salamlayırıq!",
                 "Hörmətlə,\nSales Support Team\n“Seaport Catering” MMC", "M: (+994) 102395025",
                 "sales@seaport-catering.az;corporate@seaport-catering.az", "Çıraq Plaza", "korporativ qidalanma"):
        assert text in page, text
    letter = page.split('<textarea name="body"', 1)[1].split("</textarea>", 1)[0]
    assert "Test Admin" not in letter and "Sales Support Team" in letter                # əməkdaşın imzası yox
    body = letters.fill(letters.SEAPORT_INTRO, "Rauf Əliyev")["body"]
    signed = mail.with_signature(body, "Test Admin", letters.SEAPORT_INTRO.signature)
    assert mail.without_signature(signed) == body                             # "Yenidən yarat"-da imza ikiqat olmur


def test_company_template_and_nameless_recipient_get_the_company_version(factory, opp_id, monkeypatch):
    """İkinci hazır şablon — şirkət adından ("“Seaport Catering” şirkəti Sizi salamlayır!"), müraciət sətri yox.
    Şəxsə şablon seçilib, amma alıcının adı yoxdursa (şirkətin emaili) — avtomatik şirkət variantı."""
    from app.outreach import letters

    monkeypatch.setattr(mail, "draft_email", lambda **kw: (_ for _ in ()).throw(AssertionError("AI çağırılmamalıdır")))
    client = TestClient(web.app)
    assert letters.SEAPORT_COMPANY.ref in client.get(f"/opportunities/{opp_id}").text
    for template_id, contact_id in ((letters.SEAPORT_COMPANY.ref, ""), (letters.DEFAULT, "to:info@sahil.az")):
        page = client.post(f"/opportunities/{opp_id}/email/draft",
                           data={"contact_id": contact_id, "language": "az", "template_id": template_id}).text
        body = page.split('<textarea name="body"', 1)[1].split("</textarea>", 1)[0]
        assert "“Seaport Catering” şirkəti Sizi salamlayır!" in body and "Hörmətli" not in body.split("Hörmətlə")[0]
        assert "Sales Support Team" in body and "Çıraq Plaza" in body


def test_send_writes_history_task_and_moves_stage(factory, opp_id, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setenv("SMTP_USER", "sales@dadli.az")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.send_email, "__kwdefaults__", {"smtp_factory": FakeSMTP})

    form = {"to": "Rauf@Sahil.az", "subject": "Nahar təklifi", "body": "Hörmətli Rauf bəy, sizə təklifimiz var.",
            "language": "az", "send_key": SEND_KEY}
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data=form, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    # İkiqat klik (eyni qaralama formu) — ikinci məktub getmir.
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data=form, follow_redirects=False)
    assert "artıq göndərilib" in __import__("urllib.parse").parse.unquote(r.headers["location"])
    assert len(FakeSMTP.sent) == 1

    msg = FakeSMTP.sent[0]
    # Gmail poçtu IMAP ilə oxunur — cavab şirkət poçtuna gəlməlidir (panel onu fürsətdə göstərir), əməkdaşa yox.
    assert msg["To"] == "rauf@sahil.az" and msg["Reply-To"] is None
    with factory() as s:
        out = s.scalars(select(Outreach)).one()
        opp = s.get(Opportunity, opp_id)
        tasks = [t.title for t in s.scalars(select(Task).where(Task.opportunity_id == opp_id))]
        events = [e.action for e in s.scalars(select(LeadEvent))]
    assert (out.status, out.kind, out.to_address, out.user_id) == ("sent", "email", "rauf@sahil.az", 1)
    assert opp.stage == "contacted" and opp.reply_status == "waiting"
    assert "Məktuba cavabı yoxla: rauf@sahil.az" in tasks
    assert events == ["approve", "email"]


def test_reply_to_is_the_user_when_mailbox_cannot_be_read(factory, opp_id, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setenv("SMTP_HOST", "smtp.office365.com")  # IMAP_HOST yoxdur — cavablar oxunmur
    monkeypatch.setenv("SMTP_USER", "sales@dadli.az")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(mail.send_email, "__kwdefaults__", {"smtp_factory": FakeSMTP})
    TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data={
        "to": "rauf@sahil.az", "subject": "S", "body": "Kifayət qədər uzun məktub mətni.", "send_key": SEND_KEY},
        follow_redirects=False)
    assert FakeSMTP.sent[0]["Reply-To"] == "test@neo.az"


def test_send_without_smtp_records_failure(factory, opp_id):
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data={
        "to": "rauf@sahil.az", "subject": "S", "body": "Kifayət qədər uzun məktub mətni.", "send_key": SEND_KEY},
        follow_redirects=False)
    assert "err=" in r.headers["location"]
    with factory() as s:
        out = s.scalars(select(Outreach)).one()
        assert out.status == "failed" and "SMTP_USER" in out.error
        assert s.get(Opportunity, opp_id).stage == "new"


def test_not_connected_failures_disappear_once_mail_is_set_up(factory, opp_id, monkeypatch):
    """Poçt qurulmamış edilən cəhd "Göndərilmədi: Poçt qoşulmayıb…" kimi qalırdı — poçt qurulandan sonra
    göstərilmir (başqa səbəbdən alınmayan cəhdlər görünür)."""
    c = TestClient(web.app)
    c.post(f"/opportunities/{opp_id}/email/send", data={
        "to": "rauf@sahil.az", "subject": "Köhnə cəhd", "body": "Kifayət qədər uzun məktub mətni.", "send_key": SEND_KEY})
    with factory() as s:
        s.add(Outreach(kind="email", opportunity_id=opp_id, to_address="x@y.az", subject="Başqa xəta", body="b",
                       status="failed", error="Poçt girişi alınmadı (neo@x.az)", created_at=__import__("datetime").datetime(2026, 11, 20)))
        s.commit()
    page = c.get(f"/opportunities/{opp_id}").text
    assert "Poçt qoşulmayıb" in page and "Köhnə cəhd" in page          # poçt hələ qurulmayıb — xəta görünür
    monkeypatch.setenv("SMTP_USER", "neo@chaitech.info")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    page = c.get(f"/opportunities/{opp_id}").text
    assert "Poçt qoşulmayıb" not in page and "Köhnə cəhd" not in page
    assert "Başqa xəta" in page and "Poçt girişi alınmadı" in page
    with factory() as s:
        assert len(list(s.scalars(select(Outreach)))) == 2              # sətir bazadan silinmir


def test_send_rejects_bad_address_and_keeps_draft(factory, opp_id):
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data={
        "to": "yox", "subject": "S", "body": "Redaktə olunmuş uzun məktub mətni.", "send_key": SEND_KEY},
        follow_redirects=False)
    assert r.status_code == 200 and "email ünvanı yanlışdır" in r.text
    assert "Redaktə olunmuş uzun məktub mətni." in r.text


def test_send_subject_newlines_are_flattened(factory, opp_id):
    r = TestClient(web.app).post(f"/opportunities/{opp_id}/email/send", data={
        "to": "rauf@sahil.az", "subject": "S\r\nBcc: x@y.az", "body": "Kifayət qədər uzun məktub mətni.",
        "send_key": SEND_KEY}, follow_redirects=False)
    assert r.status_code == 303
    with factory() as s:
        assert s.scalars(select(Outreach.subject)).one() == "S Bcc: x@y.az"


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


LETTER = dict(to="rauf@sahil.az", subject="Nahar təklifi", body="Hörmətli Rauf bəy, sizə təklifimiz var.",
              language="az", send_key=SEND_KEY)


def test_files_go_to_approval_are_shown_to_the_ceo_and_sent_with_the_letter(factory, opp_id, monkeypatch):
    """"Təsdiqə göndər" faylları da aparır: CEO onları təsdiq səhifəsində açır, təsdiqdən sonra göndərən
    həmin fayllarla göndərir (əvvəl fayllar təsdiqə getmirdi — CEO qoşulmuş təklif PDF-ini görmürdü)."""
    from app.db.models import LetterApproval, LetterApprovalFile

    FakeSMTP.sent = []
    monkeypatch.setenv("SMTP_USER", "sales@dadli.az")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(mail.send_email, "__kwdefaults__", dict(smtp_factory=FakeSMTP))
    c = TestClient(web.app)
    pdf = b"%PDF-1.4 teklif"
    r = c.post(f"/opportunities/{opp_id}/email/approval", data=LETTER, follow_redirects=False,
               files=[("files", ("Təklif SP-1.pdf", pdf, "application/pdf")), ("files", ("menyu.docx", b"doc", "text/html"))])
    assert r.status_code == 303 and "2%20faylla" in r.headers["location"]
    with factory() as s:
        approval = s.scalars(select(LetterApproval)).one()
        stored = list(s.scalars(select(LetterApprovalFile).order_by(LetterApprovalFile.id)))
        assert [(f.approval_id, f.filename, f.size) for f in stored] == [
            (approval.id, "Təklif SP-1.pdf", len(pdf)), (approval.id, "menyu.docx", 3)]
        pdf_id, doc_id = stored[0].id, stored[1].id

    # CEO-nun səhifəsi: fayllar görünür və açılır (PDF brauzerdə, digərləri yüklənir — tip uzantıdan)
    page = c.get("/approvals").text
    assert "Qoşulmuş fayllar (2)" in page and f"/approvals/files/{pdf_id}" in page and "Təklif SP-1.pdf" in page
    got = c.get(f"/approvals/files/{pdf_id}")
    assert got.content == pdf and got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("inline") and got.headers["x-content-type-options"] == "nosniff"
    doc = c.get(f"/approvals/files/{doc_id}")
    assert doc.headers["content-type"] == "application/octet-stream"
    assert doc.headers["content-disposition"].startswith("attachment")
    assert c.get("/approvals/files/99999").status_code == 404
    assert f"/approvals/files/{pdf_id}" in c.get(f"/opportunities/{opp_id}").text  # fürsətin "Təsdiq" bölməsi

    # "Təsdiqlə" məktubu dərhal göndərir — təsdiqə qoşulmuş fayllarla, göndərənə bildiriş gedir
    r = c.post(f"/approvals/{approval.id}/decide", data=dict(decision="approved"), follow_redirects=False)
    assert "msg=" in r.headers["location"] and len(FakeSMTP.sent) == 1
    sent = [(p.get_filename(), p.get_payload(decode=True)) for p in FakeSMTP.sent[0].iter_attachments()]
    assert sent == [("Təklif SP-1.pdf", pdf), ("menyu.docx", b"doc")]
    with factory() as s:
        from app.db.models import Notification
        out = s.scalars(select(Outreach)).one()
        assert out.status == "sent" and out.attachments == "Təklif SP-1.pdf\nmenyu.docx"
        assert out.user_id == approval.requested_by_id                  # göndərənin adından
        assert s.get(LetterApproval, approval.id).sent_at is not None
        assert any("təsdiqləndi və göndərildi" in n.text for n in s.scalars(select(Notification)))
    c.post(f"/approvals/{approval.id}/decide", data=dict(decision="approved"), follow_redirects=False)
    assert len(FakeSMTP.sent) == 1                                      # ikinci klik təkrar göndərmir


def test_approved_letter_that_cannot_be_sent_stays_approved_for_manual_sending(factory, opp_id, monkeypatch):
    from app.db.models import LetterApproval, Notification

    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    c = TestClient(web.app)
    c.post(f"/opportunities/{opp_id}/email/approval", data=LETTER, follow_redirects=False)
    with factory() as s:
        approval = s.scalars(select(LetterApproval)).one()
    r = c.post(f"/approvals/{approval.id}/decide", data=dict(decision="approved"), follow_redirects=False)
    assert "err=" in r.headers["location"]
    with factory() as s:
        row = s.get(LetterApproval, approval.id)
        assert row.status == "approved" and row.sent_at is None
        note = s.scalars(select(Notification).where(Notification.user_id == row.requested_by_id)).all()[-1]
        assert "göndərilmədi" in note.text and f"approval={row.id}#email" in note.link
        assert s.scalars(select(Outreach.send_key)).one() is None       # əl ilə göndərmək bloklanmır


def test_approval_rejects_blocked_files_and_ignores_another_opportunitys_files(factory, opp_id):
    from datetime import datetime

    from app.db.models import LetterApproval, LetterApprovalFile

    c = TestClient(web.app)
    r = c.post(f"/opportunities/{opp_id}/email/approval", data=LETTER, files=[("files", ("virus.exe", b"x", "x/y"))])
    assert r.status_code == 200 and "Bu növ fayl göndərilə bilməz" in r.text
    assert "Hörmətli Rauf bəy" in r.text  # qaralama itmir
    with factory() as s:
        assert s.scalars(select(LetterApproval)).first() is None
        # başqa fürsətin təsdiq faylı bu fürsətin məktubuna qoşula bilməz
        mine = s.get(Opportunity, opp_id)
        other = Opportunity(title="Başqa", stage="new", lead_key=None, organization_id=mine.organization_id,
                            created_at=datetime(2026, 11, 1), updated_at=datetime(2026, 11, 1))
        s.add(other)
        s.flush()
        a = LetterApproval(opportunity_id=other.id, requested_by_id=1, to_address="a@b.az", subject="S", body="B" * 30)
        s.add(a)
        s.flush()
        f = LetterApprovalFile(approval_id=a.id, filename="gizli.pdf", content_type="application/pdf", size=1, data=b"x")
        s.add(f)
        s.commit()
        a_id, f_id = a.id, f.id
    r = c.post(f"/opportunities/{opp_id}/email/approval", follow_redirects=False,
               data=dict(LETTER, files_from=str(a_id), keep_files=[str(f_id)]))
    assert r.status_code == 303
    with factory() as s:
        new = s.scalars(select(LetterApproval).where(LetterApproval.opportunity_id == opp_id)).one()
        assert s.scalars(select(LetterApprovalFile).where(LetterApprovalFile.approval_id == new.id)).first() is None
