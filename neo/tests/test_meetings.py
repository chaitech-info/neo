"""Görüşlər (0022): canlı görüşün xülasəsi və dörd ölçü — konversiya, sikl, itirmə səbəbi, proqnoz."""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import crm, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import Meeting, Opportunity, Task
from app.db.repository import LeadRepository
from app.models import RawLead


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'mt.db'}")
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
    lead = RawLead(source="boss", source_url="https://sahil.az/m", title="Sahil MMC işçilərinə nahar verir",
                   raw_text="Nahar şirkət tərəfindən təmin olunur.", entity_name="Sahil MMC",
                   status="pending_review", ai_score=0.8)
    with factory() as s:
        LeadRepository(s).save(lead.dedupe_key(), lead)
        return crm.convert_lead(s, lead.dedupe_key(), user_id=1).opportunity.id


def _post(opp_id, **data):
    return TestClient(web.app).post(f"/opportunities/{opp_id}/meetings", data=data, follow_redirects=False)


def test_meeting_is_saved_with_the_four_measures_and_updates_the_opportunity(factory, opp_id):
    today = crm.today()
    decision = today + timedelta(days=14)
    r = _post(opp_id, met_on=today.isoformat(), participants="Rauf Əliyev, satınalma müdiri",
              summary="120 işçi üçün nahar istəyirlər, dequstasiya istədilər.", stage="proposal",
              decision_on=decision.isoformat(), probability="60%", expected_value="12 000",
              next_step="Dequstasiya təşkil et", next_step_on=(today + timedelta(days=3)).isoformat())
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    with factory() as s:
        m = s.scalar(select(Meeting))
        opp = s.get(Opportunity, opp_id)
        task = s.scalar(select(Task).where(Task.title == "Dequstasiya təşkil et"))
    assert (m.stage_before, m.stage_after, m.decision_on, m.probability, m.expected_value) == (
        "new", "proposal", decision, 60, Decimal("12000.00"))
    assert (opp.stage, opp.expected_close_on, opp.win_probability, opp.expected_value) == (
        "proposal", decision, 60, Decimal("12000.00"))
    assert task is not None and task.assignee_id == 1 and "120 işçi" in task.description

    page = TestClient(web.app).get(f"/opportunities/{opp_id}").text
    for text in ("120 işçi üçün nahar istəyirlər", "Rauf Əliyev, satınalma müdiri", "Konversiya", "Yeni → Təklif göndərilib",
                 "Sikl", f"qərar {decision:%d.%m.%Y}", "Proqnoz", "60% · 12 000 AZN", "Proqnoz: 60%"):
        assert text in page, text


def test_lost_needs_a_reason_and_the_form_keeps_what_was_typed(factory, opp_id):
    summary = "Rəqib daha ucuz qiymət verib, müqavilə imzalayıblar."
    page = _post(opp_id, met_on=crm.today().isoformat(), summary=summary, stage="lost").text
    assert "itirmə səbəbini seç" in page and summary in page                 # yazılan itmir
    with factory() as s:
        assert s.scalar(select(Meeting)) is None and s.get(Opportunity, opp_id).stage == "new"

    r = _post(opp_id, met_on=crm.today().isoformat(), summary=summary, stage="lost", lost_reason="competitor",
              lost_note="Rəqib 20% ucuz", probability="50", next_step="Zəng et")
    assert r.status_code == 303
    with factory() as s:
        m, opp = s.scalar(select(Meeting)), s.get(Opportunity, opp_id)
        assert (m.lost_reason_code, m.lost_reason_note, m.probability) == ("competitor", "Rəqib 20% ucuz", 0)
        assert (opp.stage, opp.lost_reason_code, opp.win_probability) == ("lost", "competitor", 0)
        assert s.scalar(select(Task).where(Task.title == "Zəng et")) is None   # bağlanan fürsətə tapşırıq yox


@pytest.mark.parametrize("data, error", [
    ({"summary": "qısa"}, "xülasəsini yaz"),
    ({"met_on": "tomorrow"}, "gələcəkdə"),
    ({"probability": "150"}, "0 ilə 100"),
    ({"probability": "çox"}, "tam rəqəm"),
    ({"expected_value": "on min"}, "rəqəm olmalıdır"),
    ({"decision_on": "yesterday"}, "görüşdən əvvəl"),
])
def test_meeting_validation(factory, opp_id, data, error):
    today = crm.today()
    form = {"met_on": today.isoformat(), "summary": "Görüş yaxşı keçdi, təklif gözləyirlər."}
    form.update(data)
    for key in ("met_on", "decision_on"):
        if form.get(key) == "tomorrow":
            form[key] = (today + timedelta(days=1)).isoformat()
        elif form.get(key) == "yesterday":
            form[key] = (today - timedelta(days=1)).isoformat()
    page = _post(opp_id, **form).text
    assert error in page
    with factory() as s:
        assert s.scalar(select(Meeting)) is None
