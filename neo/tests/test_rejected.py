"""Rədd edilənlər: insan/AI ayrımı, geri qaytarma, "Geri al", tarixçə (lead_events)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import crm, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import LeadEvent, LeadRecord
from app.db.repository import LeadRepository
from app.models import Author, LeadCategory, LeadStatus, RawLead


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'rej.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    with factory() as s:
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    yield factory
    engine.dispose()


def _lead(factory, url, status="pending_review", score=0.75, **extra):
    lead = RawLead(source="boss", source_url=url, title=f"Lead {url}", raw_text="mətn",
                   category=LeadCategory.corporate, ai_score=score, status=status, **extra)
    key = lead.dedupe_key()
    with factory() as s:
        LeadRepository(s).save(key, lead)
    return key


def _ai_reject(factory, url):
    """Pipeline-ın etdiyi kimi: lead yazılır, AI aşağı skorla rədd edir."""
    key = _lead(factory, url, status="new")
    lead = RawLead(source="boss", source_url=url, title="t", raw_text="m", ai_score=0.2, status=LeadStatus.rejected)
    with factory() as s:
        LeadRepository(s).update_after_scoring(key, lead)
    return key


def _events(factory, key):
    with factory() as s:
        return [(e.action, e.from_status, e.to_status, e.user_id)
                for e in s.execute(select(LeadEvent).where(LeadEvent.lead_key == key).order_by(LeadEvent.id)).scalars()]


def test_human_reject_shows_on_human_tab_and_restore_returns_it(factory):
    client = TestClient(web.app)
    key = _lead(factory, "https://a.az")
    ai_key = _ai_reject(factory, "https://b.az")

    r = client.post(f"/leads/{key}/reject", follow_redirects=False)
    assert "undo=" + key in r.headers["location"]

    human_page = client.get("/leads/rejected?by=human").text
    assert "Lead https://a.az" in human_page and "Lead https://b.az" not in human_page
    assert "Rədd etdi: Test Admin" in human_page
    ai_page = client.get("/leads/rejected?by=ai").text
    assert "Lead https://b.az" in ai_page and "Lead https://a.az" not in ai_page

    r = client.post(f"/leads/{key}/restore", data={"next": "/leads/rejected?by=human"}, follow_redirects=False)
    assert r.headers["location"].startswith("/leads/rejected?by=human&msg=")
    with factory() as s:
        lead = s.get(LeadRecord, key)
        assert lead.status == "pending_review" and lead.rejected_by is None
        assert s.get(LeadRecord, ai_key).rejected_by == "ai"
    assert _events(factory, key) == [("reject", "pending_review", "rejected", 1),
                                     ("restore", "rejected", "pending_review", 1)]


def test_restored_ai_lead_rejected_again_counts_as_human(factory):
    """Köhnə filtr (ai_score < eşik) bunu "AI rədd etdi"-də göstərirdi."""
    client = TestClient(web.app)
    key = _ai_reject(factory, "https://c.az")
    client.post(f"/leads/{key}/restore")
    client.post(f"/leads/{key}/reject")

    assert "Lead https://c.az" in client.get("/leads/rejected?by=human").text
    assert "Lead https://c.az" not in client.get("/leads/rejected?by=ai").text


def test_undo_button_rendered_after_reject(factory):
    client = TestClient(web.app)
    key = _lead(factory, "https://d.az")
    page = client.post(f"/leads/{key}/reject").text  # redirect izlənir → validasiya səhifəsi
    assert f'action="/leads/{key}/restore"' in page and "Geri al" in page

    client.post(f"/leads/{key}/restore", data={"next": "/leads/pending_review"})
    with factory() as s:
        assert s.get(LeadRecord, key).status == "pending_review"


def test_restore_ignores_external_next(factory):
    client = TestClient(web.app)
    key = _ai_reject(factory, "https://e.az")
    r = client.post(f"/leads/{key}/restore", data={"next": "https://evil.example/leads"}, follow_redirects=False)
    assert r.headers["location"].startswith("/leads/rejected?")


def test_old_ai_rejected_url_redirects(factory):
    r = TestClient(web.app).get("/leads/ai_rejected", follow_redirects=False)
    assert r.headers["location"] == "/leads/rejected?by=ai"


def test_approve_writes_event(factory):
    key = _lead(factory, "https://f.az")
    with factory() as s:
        crm.convert_lead(s, key, user_id=1)
    assert _events(factory, key) == [("approve", "pending_review", "converted", 1)]


def test_linkedin_links_rendered_on_lead_card(factory):
    _lead(factory, "https://vumes.az", origin_url="https://www.linkedin.com/posts/x-1",
          author=Author(name="Aysel HR", title="HR Manager at VUMES", url="https://www.linkedin.com/in/aysel"))
    page = TestClient(web.app).get("/leads/pending_review").text
    assert 'href="https://www.linkedin.com/posts/x-1"' in page and "LinkedIn-də aç" in page
    assert 'href="https://www.linkedin.com/in/aysel"' in page and "HR Manager at VUMES" in page
