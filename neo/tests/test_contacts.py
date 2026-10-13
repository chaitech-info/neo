"""Kontakt toplama: mətn (email/telefon), AI şəxsləri, LinkedIn müəllifi, şirkət saytı, saxlama, fürsət səhifəsi."""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import contacts, crm, pipeline, web
from app.auth import security
from app.auth.users import create_user
from app.db.migrate import upgrade_to_head
from app.db.models import Contact
from app.db.repository import LeadRepository
from app.models import Author, LeadCategory, LeadStatus, RawLead
from app.scrapers.base import BaseScraper


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ct.db'}")
    upgrade_to_head(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(security, "SessionLocal", factory)
    monkeypatch.setattr(web, "SessionLocal", factory)
    with factory() as s:
        create_user(s, email="test@neo.az", full_name="Test Admin", role_code="admin", password="x" * 12)
    yield factory
    engine.dispose()


@pytest.mark.parametrize("raw, expected", [
    ("+994 50 123 45 67", "+994501234567"),
    ("(012) 555-12-34", "+994125551234"),
    ("055 444 33 22", "+994554443322"),
    ("994701112233", "+994701112233"),
    ("Tel: 0 12 404 00 00", "+994124040000"),
])
def test_phone_formats(raw, expected):
    assert contacts.normalize_phone(raw) == expected


def test_extract_from_text_skips_job_boards_and_junk():
    text = ("CV: hr@vumes.az və ya cv@humanpro.az, info@boss.az. Logo: logo@2x.png. "
            "Zəng: +994 50 123 45 67, 050 123 45 67 (eyni). İl: 2026-10-14, məbləğ 1500000")
    found = contacts.extract_from_text(text)
    assert [f.email for f in found if f.email] == ["hr@vumes.az"]
    assert [f.phone for f in found if f.phone] == ["+994501234567"]


def test_from_ai_keeps_named_people_and_drops_empty():
    people = [{"name": "Aysel Məmmədova", "role": "HR menecer", "email": "Aysel@Vumes.az", "phone": "050 111 22 33"},
              {"name": "", "role": "", "email": "", "phone": ""}]
    found = contacts.from_ai(people)
    assert len(found) == 1
    assert (found[0].name, found[0].title, found[0].email, found[0].phone) == (
        "Aysel Məmmədova", "HR menecer", "aysel@vumes.az", "+994501112233")


def test_crawlable_skips_social_and_job_sites():
    assert contacts.crawlable("https://vumes.az")
    assert not contacts.crawlable("https://www.linkedin.com/search/results/companies/?keywords=x")
    assert not contacts.crawlable("https://boss.az/vacancies/1")
    assert not contacts.crawlable(None)


class _Site:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kwargs):
        if url not in self.pages:
            raise httpx.ConnectError("no")
        return httpx.Response(200, text=self.pages[url], request=httpx.Request("GET", url))

    def close(self):
        pass


def test_website_crawl_reads_contact_page(monkeypatch):
    site = _Site({
        "https://vumes.az": '<a href="/az/elaqe">Əlaqə</a><a href="/blog">Blog</a><p>Xoş gəlmisiniz</p>',
        "https://vumes.az/az/elaqe": '<p>Ofis: +994 12 310 20 30</p><a href="mailto:sales@vumes.az">Yaz</a>',
    })
    crawler = contacts.WebsiteContacts(client=site)
    crawler.respect_robots = False
    monkeypatch.setattr(crawler, "polite_sleep", lambda: None)
    found = crawler.crawl("https://vumes.az")
    assert {f.email for f in found if f.email} == {"sales@vumes.az"}
    assert {f.phone for f in found if f.phone} == {"+994123102030"}
    assert all(f.source == "website" for f in found)


def test_save_contacts_dedupes_and_fills_legacy_fields(factory):
    lead = RawLead(source="linkedin", source_url="https://vumes.az", title="VUMES", raw_text="hr@vumes.az 050 123 45 67",
                   author=Author(name="Aysel", title="HR", url="https://www.linkedin.com/in/aysel"))
    key = lead.dedupe_key()
    with factory() as s:
        record = LeadRepository(s).save(key, lead)
        found = contacts.collect(record, [{"name": "Aysel", "role": "HR", "email": "hr@vumes.az", "phone": ""}])
        assert contacts.save_contacts(s, record, found) == 3  # müəllif, AI (email ilə), telefon
        assert contacts.save_contacts(s, record, found) == 0  # təkrar — heç nə
        s.commit()
        assert record.contact_email == "hr@vumes.az" and record.contact_phone == "+994501234567"
        sources = sorted(c.source for c in s.scalars(select(Contact)))
    assert sources == ["ai", "author", "text"]


class _OneLead(BaseScraper):
    source_name = "boss"

    def fetch_leads(self):
        return [RawLead(source="boss", source_url="https://sahil.az", title="Sahil MMC — nahar",
                        raw_text="Nahar şirkət tərəfindən. Əlaqə: 055 444 33 22", category=LeadCategory.corporate)]


class _Scorer:
    version, model = "v2", "fake"

    def score_batch(self, leads):
        for lead in leads:
            lead.ai_score, lead.status, lead.tier = 0.8, LeadStatus.pending_review, "B"
            lead.people = [{"name": "Rauf Əliyev", "role": "Direktor", "email": "", "phone": ""}]
        return leads


def test_pipeline_saves_contacts_for_pending_leads(factory, monkeypatch):
    monkeypatch.setattr(pipeline, "make_scorer", lambda: _Scorer())
    with factory() as s:
        result = pipeline.run_pipeline(_OneLead, s, source_key="boss")
    assert result.pending == 1 and result.contacts == 2
    with factory() as s:
        rows = {(c.name, c.phone, c.source) for c in s.scalars(select(Contact))}
    assert rows == {("Rauf Əliyev", None, "ai"), (None, "+994554443322", "text")}


def test_contacts_follow_lead_to_opportunity_page(factory):
    lead = RawLead(source="boss", source_url="https://sahil.az", title="Sahil", raw_text="x",
                   entity_name="Sahil MMC", status="pending_review", ai_score=0.8)
    key = lead.dedupe_key()
    with factory() as s:
        record = LeadRepository(s).save(key, lead)
        contacts.save_contacts(s, record, [contacts.Found(name="Rauf Əliyev", title="Direktor", source="ai")])
        s.commit()
        opp = crm.convert_lead(s, key, user_id=1).opportunity
        assert s.scalars(select(Contact)).one().organization_id == opp.organization_id

    client = TestClient(web.app)
    page = client.get(f"/opportunities/{opp.id}").text
    assert "Rauf Əliyev" in page and "Direktor" in page

    r = client.post(f"/opportunities/{opp.id}/contacts",
                    data={"name": "Leyla", "email": "Leyla@Sahil.az", "phone": "050 765 43 21"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    with factory() as s:
        added = s.scalars(select(Contact).where(Contact.source == "manual")).one()
    assert (added.email, added.phone) == ("leyla@sahil.az", "+994507654321")

    assert "err=" in client.post(f"/opportunities/{opp.id}/contacts", data={"email": "yox"},
                                 follow_redirects=False).headers["location"]
    client.post(f"/contacts/{added.id}/delete", data={"back": f"/opportunities/{opp.id}"})
    with factory() as s:
        assert s.scalar(select(Contact).where(Contact.id == added.id)) is None
