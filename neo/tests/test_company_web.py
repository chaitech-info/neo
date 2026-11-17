"""Şirkət analizi GPT veb axtarışı ilə (app/company_analysis.py::web_research): OpenAI saxtadır."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import company_analysis, web
from app.db.models import Organization
from tests.test_company_db import AI, factory  # noqa: F401 — pytest fixture

WEB = {**AI, "summary": "Sahil Bakıda logistika şirkətidir, 3 anbarı var.", "employees": "≈450 (LinkedIn)",
       "founded": "2009", "website": "sahil.az", "phone": "+994 12 555 11 22", "email": "info@sahil.az",
       "address": "Bakı, Xətai r., Babək pr. 10", "locations": ["Bakı ofisi", "Sumqayıt anbarı"],
       "key_people": [{"name": "Rauf Əliyev", "role": "Baş direktor"}, {"name": "", "role": "boş"}],
       "news": [{"title": "Sahil yeni anbar açdı", "date": "2026-10-02", "url": "https://news.az/sahil"},
                {"title": "Linksiz xəbər", "date": "", "url": "javascript:alert(1)"}],
       "sources": ["https://sahil.az", "https://www.linkedin.com/company/sahil", "ftp://x", "bilinmir"]}


class FakeOpenAI:
    def __init__(self, out=None, error=None):
        self.out, self.error, self.calls = out, error, []
        self.responses = self

    def with_options(self, **kw):
        return self

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(output_text=json.dumps(self.out))


def _patch(monkeypatch, fake, chat_calls):
    monkeypatch.setattr(company_analysis, "_client", lambda c: fake)
    monkeypatch.setattr(company_analysis, "site_text", lambda url, crawler=None: ("", []))

    def fake_chat(client, model, system, user, schema, name="out", max_tokens=700):
        chat_calls.append(user)
        return dict(AI), None
    monkeypatch.setattr(company_analysis, "chat_json", fake_chat)


def test_analysis_uses_web_search_and_keeps_only_real_links(factory, monkeypatch):  # noqa: F811
    fake, chat = FakeOpenAI(WEB), []
    _patch(monkeypatch, fake, chat)
    page = TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"}).text

    call = fake.calls[0]
    assert call["tools"] == [{"type": "web_search"}] and call["text"]["format"]["schema"]["required"][-1] == "sources"
    assert "COMPANY: Sahil MMC" in call["input"] and not chat  # köhnə üsula keçilmədi
    for text in ("≈450 (LinkedIn)", "Rauf Əliyev", "Sahil yeni anbar açdı", "https://news.az/sahil", "Mənbələr (2)",
                 "Sumqayıt anbarı", "internetdə araşdırıb"):
        assert text in page
    assert "javascript:" not in page and "ftp://x" not in page

    with factory() as s:
        org = s.scalars(select(Organization)).one()
        assert org.email == "info@sahil.az" and org.phone == "+994 12 555 11 22" and org.domain == "sahil.az"
        saved = json.loads(org.analysis)
        assert saved["web"] is True and saved["ai"]["sources"] == ["https://sahil.az", "https://www.linkedin.com/company/sahil"]


def test_web_contacts_do_not_overwrite_what_staff_entered(factory, monkeypatch):  # noqa: F811
    with factory() as s:
        s.add(Organization(name="Sahil MMC", name_normalized="sahil", email="sales@sahil.az", phone="+994 50 000 00 00"))
        s.commit()
    _patch(monkeypatch, FakeOpenAI(WEB), [])
    TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"})
    with factory() as s:
        org = s.scalars(select(Organization)).one()
        assert org.email == "sales@sahil.az" and org.phone == "+994 50 000 00 00"


def test_web_search_failure_falls_back_to_site_and_database(factory, monkeypatch):  # noqa: F811
    chat = []
    _patch(monkeypatch, FakeOpenAI(error=TimeoutError("slow")), chat)
    page = TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"}).text
    assert chat and AI["summary"] in page and "internetdə araşdırıb" not in page


def test_web_search_can_be_switched_off(factory, monkeypatch):  # noqa: F811
    monkeypatch.setenv("COMPANY_WEB_SEARCH", "0")
    fake, chat = FakeOpenAI(WEB), []
    _patch(monkeypatch, fake, chat)
    TestClient(web.app).post("/tools/company", data={"name": "Sahil MMC"})
    assert not fake.calls and chat
