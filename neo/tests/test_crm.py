"""Öz CRM: "Təsdiqlə" → fürsət + tapşırıq, mərhələlər, tapşırıq ekranları."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import crm, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import LeadRecord, Opportunity, Organization, Task
from app.db.repository import LeadRepository
from app.models import LeadCategory, RawLead

NOW = datetime(2026, 9, 30, 10, 0)


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'crm.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    monkeypatch.setattr(crm, "_now", lambda: NOW)
    with factory() as s:
        # conftest-dəki "daxil olmuş admin" id=1 — bazada da olsun (FK üçün).
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    yield factory
    engine.dispose()


def _lead(factory, url, *, category=LeadCategory.corporate, deadline=None, score=0.75,
          entity="Sahil Transport MMC", status="pending_review", source="boss"):
    lead = RawLead(source=source, source_url=url, title=f"Lead {url}", raw_text="mətn",
                   entity_name=entity, category=category, ai_score=score, status=status,
                   deadline_at=deadline)
    key = lead.dedupe_key()
    with factory() as s:
        LeadRepository(s).save(key, lead)
        s.commit()
    return key


def test_approve_creates_opportunity_task_and_organization(factory):
    key = _lead(factory, "https://sahil.az", score=0.85)

    with factory() as s:
        conv = crm.convert_lead(s, key, user_id=1)
        lead = s.get(LeadRecord, key)
        org = s.get(Organization, conv.opportunity.organization_id)

        assert lead.status == "converted"
        assert lead.crm_opportunity_id == str(conv.opportunity.id)
        assert lead.reviewed_by_id == 1
        assert conv.opportunity.stage == "new" and conv.opportunity.owner_id == 1
        assert org.name == "Sahil Transport MMC"
        assert conv.task.title == "İlk əlaqə: Sahil Transport MMC"
        assert conv.task.due_at == NOW + timedelta(days=1)
        assert conv.task.priority == "high"  # AI skoru >= 0.8
        assert conv.task.assignee_id == 1 and conv.task.created_by_id is None

    with factory() as s, pytest.raises(crm.CrmError):
        crm.convert_lead(s, key, user_id=1)  # artıq emal olunub


def test_tender_task_is_due_before_the_deadline(factory):
    key = _lead(factory, "https://etender.gov.az/main/competition/detail/1", source="etender",
                category=LeadCategory.tender, deadline=NOW + timedelta(days=9), entity="ŞABRAN RAYON İCRA HAKİMİYYƏTİ")
    near = _lead(factory, "https://etender.gov.az/main/competition/detail/2", source="etender",
                 category=LeadCategory.tender, deadline=NOW + timedelta(hours=30), entity="SOFAZ")

    with factory() as s:
        far_task = crm.convert_lead(s, key, 1).task
        near_task = crm.convert_lead(s, near, 1).task

    assert far_task.title.startswith("Tender sənədlərini yoxla")
    assert far_task.due_at == NOW + timedelta(days=7) and far_task.priority == "normal"
    assert near_task.due_at == NOW + timedelta(hours=4)  # minimum hazırlıq vaxtı
    assert near_task.priority == "high"


def test_only_pending_leads_can_be_converted(factory):
    rejected = _lead(factory, "https://x.az/1", status="rejected")
    with factory() as s, pytest.raises(crm.CrmError):
        crm.convert_lead(s, rejected, 1)


def test_stage_rules(factory):
    key = _lead(factory, "https://sahil.az")
    with factory() as s:
        opp = crm.convert_lead(s, key, 1).opportunity
        crm.add_task(s, opp, "Təklif göndər", None, 1, 1)

        with pytest.raises(crm.CrmError):
            crm.change_stage(s, opp, "lost")  # səbəbsiz olmaz
        crm.change_stage(s, opp, "lost", "price", "bahalı")
        assert opp.closed_at == NOW and opp.lost_reason_code == "price"
        assert {t.status for t in crm.opp_tasks(s, opp.id)} == {"cancelled"}

        crm.change_stage(s, opp, "contacted")  # yenidən açmaq olar
        assert opp.closed_at is None and opp.lost_reason_code is None

        opp.tender_id = 1  # tender fürsəti
        with pytest.raises(crm.CrmError):
            crm.change_stage(s, opp, "negotiation")


def test_web_approve_and_screens(factory):
    key = _lead(factory, "https://sahil.az")
    client = TestClient(web.app)

    resp = client.post(f"/leads/{key}/approve", follow_redirects=False)
    assert resp.status_code == 303
    assert "opp=" in resp.headers["location"] and "err" not in resp.headers["location"]

    with factory() as s:
        opp = s.scalars(select(Opportunity)).one()
    listing = client.get("/opportunities")
    assert listing.status_code == 200 and f'href="/opportunities/{opp.id}"' in listing.text

    detail = client.get(f"/opportunities/{opp.id}")
    assert detail.status_code == 200 and "İlk əlaqə" in detail.text

    assert client.post(f"/opportunities/{opp.id}/tasks",
                       data={"title": "Görüş təyin et", "due_at": "2026-10-02T11:00", "priority": "high"},
                       follow_redirects=False).status_code == 303
    assert client.post(f"/opportunities/{opp.id}/stage", data={"stage": "contacted"},
                       follow_redirects=False).status_code == 303

    with factory() as s:
        tasks = s.scalars(select(Task).where(Task.title == "Görüş təyin et")).all()
        assert tasks[0].due_at == datetime(2026, 10, 2, 7, 0)  # Bakı 11:00 → UTC 07:00
        assert s.get(Opportunity, opp.id).stage == "contacted"
        first = s.scalars(select(Task).where(Task.title.startswith("İlk əlaqə"))).one()

    page = client.get("/tasks?who=all")
    assert page.status_code == 200 and "Görüş təyin et" in page.text
    assert client.post(f"/tasks/{first.id}/status", data={"status": "done", "back": "/tasks"},
                       follow_redirects=False).status_code == 303
    with factory() as s:
        assert s.get(Task, first.id).status == "done"


def test_web_rejects_bad_input(factory):
    key = _lead(factory, "https://sahil.az")
    client = TestClient(web.app)
    client.post(f"/leads/{key}/approve")
    with factory() as s:
        opp_id = s.scalars(select(Opportunity.id)).one()

    bad_stage = client.post(f"/opportunities/{opp_id}/stage", data={"stage": "lost"}, follow_redirects=False)
    assert "err=" in bad_stage.headers["location"]
    bad_value = client.post(f"/opportunities/{opp_id}/details", data={"expected_value": "çox"}, follow_redirects=False)
    assert "err=" in bad_value.headers["location"]
    assert client.get("/opportunities/9999", follow_redirects=False).headers["location"].startswith("/opportunities?err=")
    open_redirect = client.post("/tasks/1/status", data={"status": "done", "back": "//evil.com"}, follow_redirects=False)
    assert open_redirect.headers["location"].startswith("/tasks")
