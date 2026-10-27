"""Öz CRM: "Təsdiqlə" → fürsət + tapşırıq, mərhələlər, tapşırıq ekranları."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
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
        assert conv.task.due_on == date(2026, 10, 1)  # sabah (Bakı tarixi), saatsız
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
    assert far_task.due_on == date(2026, 10, 7) and far_task.priority == "normal"  # son tarixdən 2 gün əvvəl
    assert near_task.due_on == date(2026, 9, 30)  # bu gündən tez ola bilməz
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
                       data={"title": "Görüş təyin et", "due_on": "2026-10-02", "priority": "high"},
                       follow_redirects=False).status_code == 303
    # Tapşırıqda saat yoxdur — saatlı dəyər rədd olunur, tapşırıq yaranmır.
    bad = client.post(f"/opportunities/{opp.id}/tasks", data={"title": "Saatlı", "due_on": "2026-10-02T11:00"},
                      follow_redirects=False)
    assert "err=" in bad.headers["location"]
    assert client.post(f"/opportunities/{opp.id}/stage", data={"stage": "contacted"},
                       follow_redirects=False).status_code == 303

    with factory() as s:
        tasks = s.scalars(select(Task).where(Task.title == "Görüş təyin et")).all()
        assert tasks[0].due_on == date(2026, 10, 2)
        assert not s.scalars(select(Task).where(Task.title == "Saatlı")).all()
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
    backslash = client.post("/tasks/1/status", data={"status": "done", "back": "/\\evil.com"}, follow_redirects=False)
    assert backslash.headers["location"].startswith("/tasks?")

    # Numeric(14, 2) overflow, NaN, Infinity və mənfi dəyər — 500 yox, xəta mesajı.
    for value in ("100000000000000", "NaN", "Infinity", "-5"):
        r = client.post(f"/opportunities/{opp_id}/details", data={"expected_value": value, "owner_id": "1"},
                        follow_redirects=False)
        assert "err=" in r.headers["location"], value
    ok = client.post(f"/opportunities/{opp_id}/details", data={"expected_value": "1500,50", "owner_id": "1"},
                     follow_redirects=False)
    assert "msg=" in ok.headers["location"]

    ghost = client.post(f"/opportunities/{opp_id}/tasks", data={"title": "X", "assignee_id": "999"},
                        follow_redirects=False)
    assert "err=" in ghost.headers["location"]  # olmayan məsul → FK 500 yox
    long_email = client.post(f"/opportunities/{opp_id}/contacts", data={"email": "a" * 250 + "@x.az"},
                             follow_redirects=False)
    assert "err=" in long_email.headers["location"]

    # Səhifələrdə xam JSON yox, panel səhifəsi; API-lər JSON qalır.
    bad_id = client.get("/opportunities/abc")
    assert bad_id.status_code == 422 and "Yanlış sorğu" in bad_id.text
    missing = client.get("/bele-sehife-yoxdur")
    assert missing.status_code == 404 and "Səhifə tapılmadı" in missing.text
    assert client.get("/api/logs?after=x").status_code == 422
    assert "detail" in client.get("/api/logs?after=x").json()


def test_double_approve_is_not_a_server_error(factory, monkeypatch):
    key = _lead(factory, "https://sahil.az")

    def race(*a, **k):
        # Paralel ikinci sorğu: lead hələ pending görünürdü, fürsəti isə birinci yaradıb → UNIQUE(lead_key).
        raise IntegrityError("INSERT INTO opportunities", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(web.crm, "convert_lead", race)
    r = TestClient(web.app).post(f"/leads/{key}/approve", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]


def test_deactivated_owner_is_kept_on_save(factory):
    key = _lead(factory, "https://sahil.az")
    with factory() as s:
        owner = create_user(s, email="old@neo.az", full_name="Köhnə Satış", role_code="sales", password="x" * 12)
        opp = crm.convert_lead(s, key, owner.id).opportunity
        owner.is_active = False
        s.commit()
    client = TestClient(web.app)
    page = client.get(f"/opportunities/{opp.id}").text
    assert f'<option value="{owner.id}" selected>Köhnə Satış (deaktiv)</option>' in page
    r = client.post(f"/opportunities/{opp.id}/details", data={"expected_value": "", "owner_id": str(owner.id)},
                    follow_redirects=False)
    assert "msg=" in r.headers["location"]
    with factory() as s:
        assert s.get(Opportunity, opp.id).owner_id == owner.id
