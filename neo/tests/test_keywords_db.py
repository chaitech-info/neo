"""Açar sözlər və maraq sahələri bazadan: scraper seçimi, AI promptu, panel redaktəsi."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import keywords, web
from app.ai_filter import AIFilter
from app.ai_triage import prompt as triage_prompt
from app.auth import security
from app.db.migrate import upgrade_to_head
from app.db.models import InterestArea, Keyword
from app.keywords import snapshot as REAL_SNAPSHOT  # conftest bunu əvəz etməmişdən əvvəl
from app.scrapers.base import BaseScraper


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'kw.db'}")
    upgrade_to_head(engine)  # 0002 koddakı siyahıları maraq sahələri ilə birlikdə yazır
    monkeypatch.setattr(keywords, "snapshot", REAL_SNAPSHOT)
    monkeypatch.setattr(keywords, "_session", lambda: Session(engine))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    keywords.invalidate()
    yield factory
    keywords.invalidate()
    engine.dispose()


class _Dummy(BaseScraper):
    source_name = "dummy"

    def fetch_leads(self):
        return []


def _add(factory, term, area_code="staff_meals", scope="all", negative=False, match="prefix", active=True):
    with factory() as s:
        area = s.scalars(select(InterestArea).where(InterestArea.code == area_code)).one()
        s.add(Keyword(interest_area_id=area.id, term=term, language="az", match=match,
                      applies_to=scope, is_negative=negative, is_active=active))
        s.commit()
    keywords.invalidate()


def test_seeded_scopes_match_the_code_lists(db):
    tender_pos, tender_neg = keywords.terms_for("tender")
    news_pos, _ = keywords.terms_for("news")
    tender = {t.text for t in tender_pos}
    news = {t.text for t in news_pos}

    assert {"keytrinq", "komandaqurma", "tədbir"} <= tender       # MEAL + EVENT
    assert "zavod" not in tender                                   # EXPANSION yalnız xəbərdə
    assert {"keytrinq", "zavod", "korporativ tədbir"} <= news
    assert "komandaqurma" not in news
    assert tender_neg == []


def test_match_modes():
    word = keywords.Term("ət", "word")
    prefix = keywords.Term("qidalanma", "prefix")
    assert keywords.matches("Ət məhsullarının satın alınması", word)
    assert not keywords.matches("Ətraf mühitin mühafizəsi", word)       # tam söz
    assert keywords.matches("İşçilərin QİDALANMASININ təşkili", prefix)   # şəkilçi + böyük hərf


def test_new_db_keyword_is_used_immediately_and_negative_blocks(db, monkeypatch):
    titles = ["Xəstəxana pasiyentlərinin pəhriz rasionu", "Tədbir üçün hədiyyəlik malların alınması"]
    scraper = _Dummy()
    assert scraper.select_relevant(titles, [], kind="tender") == [False, True]  # "tədbir"

    _add(db, "pəhriz", scope="tender")
    _add(db, "hədiyyəlik", area_code="events", scope="tender", negative=True)
    # AI da ikincini seçsə belə, mənfi söz bağlayır.
    monkeypatch.setattr("app.ai_triage.triage", lambda t, kind="news", client=None: {1})

    assert scraper.select_relevant(titles, [], kind="tender") == [True, False]


def test_inactive_keywords_and_areas_are_ignored(db):
    _add(db, "pəhriz", scope="tender", active=False)
    assert "pəhriz" not in {t.text for t in keywords.terms_for("tender")[0]}

    with db() as s:
        s.scalars(select(InterestArea).where(InterestArea.code == "events")).one().is_active = False
        s.commit()
    keywords.invalidate()
    assert "komandaqurma" not in {t.text for t in keywords.terms_for("tender")[0]}


def test_ai_prompts_use_interest_areas_from_db(db):
    with db() as s:
        area = s.scalars(select(InterestArea).where(InterestArea.code == "staff_meals")).one()
        area.ai_description = "Xəstəxana və məktəblərə pəhriz yeməkləri"
        s.commit()
    keywords.invalidate()

    assert "Xəstəxana və məktəblərə pəhriz yeməkləri" in AIFilter().system_prompt()
    assert "Xəstəxana və məktəblərə pəhriz yeməkləri" in triage_prompt("tender")


def test_fallback_to_code_when_db_is_unavailable():
    # conftest: snapshot → None
    assert keywords.terms_for("tender") is None
    assert "paketlənmiş/porsiyalı nahar" in AIFilter().system_prompt()
    assert _Dummy().select_relevant(["Keytrinq xidməti"], ["keytrinq"], kind="tender") == [True]


def test_panel_edits_keywords_and_areas(db):
    client = TestClient(web.app)
    with db() as s:
        area_id = s.scalars(select(InterestArea.id).where(InterestArea.code == "staff_meals")).one()

    r = client.post("/settings/keywords", data={"terms": "pəhriz yeməyi\nkeytrinq\nпитание сотрудников",
                                                 "interest_area_id": area_id, "applies_to": "all"},
                    follow_redirects=False)
    assert "2%20a%C3%A7ar%20s%C3%B6z%20%C9%99lav%C9%99" in r.headers["location"]  # "2 açar söz əlavə", keytrinq artıq var
    with db() as s:
        new = s.scalars(select(Keyword).where(Keyword.term == "питание сотрудников")).one()
        assert new.language == "ru" and new.match == "phrase"
        kw_id = new.id
    assert "питание сотрудников" in {t.text for t in keywords.terms_for("news")[0]}

    client.post(f"/settings/keywords/{kw_id}/toggle")
    assert "питание сотрудников" not in {t.text for t in keywords.terms_for("news")[0]}
    client.post(f"/settings/keywords/{kw_id}/delete")
    with db() as s:
        assert s.get(Keyword, kw_id) is None

    client.post("/settings/areas", data={"code": "hospitals", "name": "Xəstəxanalar", "priority": "1",
                                         "ai_description": "Xəstəxana qidalanması"})
    assert "Xəstəxanalar" in keywords.company_profile()
    bad = client.post("/settings/areas", data={"code": "Bad Code!", "name": "x"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]

    page = client.get("/settings/keywords?q=keytrinq")
    assert page.status_code == 200 and "keytrinq" in page.text
