"""LinkedIn avtomatik şərhi: yalnız alıcı postu, söndürmə açarı, bir posta bir şərh, gündəlik limit, xəta fürsəti bloklamır."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import LeadRecord, Opportunity, Outreach
from app.db.repository import LeadRepository
from app.models import Author, LeadCategory, RawLead
from app.outreach import linkedin_comment

POST = "https://www.linkedin.com/posts/aysel_catering-activity-7251234567890123456-AbCd"


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'li.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    with factory() as s:
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    yield factory
    engine.dispose()


@pytest.fixture()
def unipile(monkeypatch):
    """Saxta OpenAI + Unipile; göndərilən şərhlər `calls`-da."""
    calls = []
    monkeypatch.setenv("LINKEDIN_AUTOCOMMENT", "on")
    for name, value in (("UNIPILE_DSN", "api1.unipile.com:13111"), ("UNIPILE_API_KEY", "k"), ("UNIPILE_ACCOUNT_ID", "acc")):
        monkeypatch.setenv(name, value)

    def handler(request: httpx.Request):
        calls.append((request.method, str(request.url), request.content))
        if request.method == "GET":
            return httpx.Response(200, json={"social_id": "urn:li:activity:7251234567890123456"})
        return httpx.Response(201, json={"comment_id": "c-1"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    content = json.dumps({"comment": "Salam! Biz məhz bu xidməti göstəririk — +994 50 000 00 00 ilə əlaqə saxlayın."})
    ai = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None))))
    real = linkedin_comment.auto_comment
    monkeypatch.setattr(linkedin_comment, "auto_comment", lambda *a, **kw: real(*a, **{"client": ai, "http": http, **kw}))
    return calls


def _lead(factory, url=POST, raw="Catering şirkəti ilə əməkdaşlıq axtarırıq, Bakı", source="linkedin", origin=None):
    lead = RawLead(source=source, source_url=url, title="Catering axtarırıq", raw_text=raw, origin_url=origin or url,
                   author=Author(name="Aysel"), category=LeadCategory.corporate, status="pending_review", ai_score=0.9)
    key = lead.dedupe_key()
    with factory() as s:
        LeadRepository(s).save(key, lead)
    return key


def test_post_id_from_urls():
    assert linkedin_comment.post_id(POST) == "7251234567890123456"
    assert linkedin_comment.post_id("https://www.linkedin.com/feed/update/urn:li:activity:7251234567890123456/") == "7251234567890123456"
    assert linkedin_comment.post_id("https://vumes.az") is None


def test_approving_buyer_post_comments_once(factory, unipile):
    key = _lead(factory)
    client = TestClient(web.app)
    r = client.post(f"/leads/{key}/approve", follow_redirects=False)
    assert "LinkedIn" in __import__("urllib.parse").parse.unquote(r.headers["location"])

    method, url, body = unipile[-1]
    assert method == "POST" and url == "https://api1.unipile.com:13111/api/v1/posts/urn:li:activity:7251234567890123456/comments"
    assert json.loads(body) == {"account_id": "acc", "text": "Salam! Biz məhz bu xidməti göstəririk — +994 50 000 00 00 ilə əlaqə saxlayın."}
    with factory() as s:
        row = s.scalars(select(Outreach)).one()
        opp_id = s.scalar(select(Opportunity.id))
    assert (row.status, row.external_id, row.to_address, row.language) == ("sent", "c-1", POST, "az")

    # "Yenidən cəhd" artıq yazılmış posta ikinci şərh yazmır
    r = client.post(f"/opportunities/{opp_id}/linkedin-comment", follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert len([c for c in unipile if c[0] == "POST"]) == 1


def test_employer_signal_and_other_sources_are_not_commented(factory, unipile):
    # Brauzer testində tapılıb: işəgötürən lead-inin origin_url-u LinkedIn postudur — şərh yazılmamalıdır,
    # hətta mətn prefiksi fərqli olsa da (source_url şirkət saytıdır).
    employer = _lead(factory, url="https://vumes.az", origin=POST, raw="Anbardar tələb olunur. Nahar şirkət tərəfindən.")
    search = _lead(factory, url="https://www.linkedin.com/search/results/companies/?keywords=Sahil", origin=POST + "-3")
    signal = _lead(factory, url=POST + "-4", raw="[SİQNAL: işəgötürən ...]\nElan: " + POST)
    company = _lead(factory, url=POST + "-2", source="linkedin_companies")
    client = TestClient(web.app)
    for key in (employer, search, signal, company):
        client.post(f"/leads/{key}/approve")
    assert unipile == []
    with factory() as s:
        assert s.scalars(select(Outreach)).all() == []


def test_kill_switch(factory, unipile, monkeypatch):
    monkeypatch.setenv("LINKEDIN_AUTOCOMMENT", "off")
    TestClient(web.app).post(f"/leads/{_lead(factory)}/approve")
    assert unipile == []


def test_daily_cap(factory, unipile, monkeypatch):
    monkeypatch.setenv("LINKEDIN_COMMENT_DAILY_CAP", "1")
    client = TestClient(web.app)
    client.post(f"/leads/{_lead(factory)}/approve")
    client.post(f"/leads/{_lead(factory, url=POST.replace('7251', '7252'))}/approve")
    with factory() as s:
        statuses = sorted(r.status for r in s.scalars(select(Outreach)))
    assert statuses == ["sent", "skipped"]


def test_failure_does_not_block_opportunity_and_can_be_retried(factory, unipile, monkeypatch):
    monkeypatch.delenv("UNIPILE_API_KEY")
    key = _lead(factory)
    client = TestClient(web.app)
    client.post(f"/leads/{key}/approve")
    with factory() as s:
        assert s.get(LeadRecord, key).status == "converted"
        row = s.scalars(select(Outreach)).one()
        opp_id = s.scalar(select(Opportunity.id))
    assert row.status == "failed" and "UNIPILE" in row.error
    assert "Yenidən cəhd et" in client.get(f"/opportunities/{opp_id}").text

    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    r = client.post(f"/opportunities/{opp_id}/linkedin-comment", follow_redirects=False)
    assert "msg=" in r.headers["location"]
    with factory() as s:
        assert sorted(r.status for r in s.scalars(select(Outreach))) == ["failed", "sent"]
