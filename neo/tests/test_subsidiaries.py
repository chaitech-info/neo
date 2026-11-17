"""Alt şirkətlər (app/subsidiaries.py): AI + veb axtarışı saxtadır, sayt saxtadır — heç bir xarici sorğu getmir."""
import json
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import companies, subsidiaries, web
from app.db.models import Organization
from tests.test_company_db import factory, fake_ai  # noqa: F401 — pytest fixture-ləri


def _auto(page: str) -> bool:
    """Bölmə araşdırmanı özü başladırmı (section teqində data-sub-auto)."""
    return bool(re.search(r'<section[^>]*data-sub-auto', page))

FOUND = [
    {"name": "Sahil Logistika MMC", "website": "sahil-log.az", "relation": "törəmə şirkət",
     "source_url": "https://sahil.az/sirketler", "in_azerbaijan": True},
    {"name": "Sahil Aqro MMC", "website": "", "relation": "holdinqin üzvü",
     "source_url": "https://news.az/sahil-aqro", "in_azerbaijan": True},
]


class FakeAI:
    """OpenAI client-in responses.create-i: sorğunu yadda saxlayır, verilən JSON-u qaytarır."""

    def __init__(self, found):
        self.found, self.calls = found, []
        self.responses = self

    def with_options(self, **kw):
        return self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(output_text=json.dumps({"subsidiaries": self.found}))


class FakeSite:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, timeout=None):
        return SimpleNamespace(text=self.pages.get(url, "<html></html>"))


def test_clean_keeps_only_sourced_local_unique_subsidiaries():
    found = FOUND + [
        {"name": "Uydurma MMC", "website": "", "relation": "", "source_url": "", "in_azerbaijan": True},  # mənbə yoxdur
        {"name": "Sahil GmbH", "website": "", "relation": "", "source_url": "https://x.de", "in_azerbaijan": False},
        {"name": "Sahil Holding", "website": "", "relation": "", "source_url": "https://x.az", "in_azerbaijan": True},
        {"name": "SAHIL LOGISTIKA", "website": "", "relation": "", "source_url": "https://y.az", "in_azerbaijan": True},
    ]
    out = subsidiaries._clean(found, "Sahil Holding")
    assert [f["name"] for f in out] == ["Sahil Logistika MMC", "Sahil Aqro MMC"]
    assert out[0]["website"] == "https://sahil-log.az" and out[1]["website"] is None
    assert out[0]["source"] == "https://sahil.az/sirketler" and out[0]["relation"] == "törəmə şirkət"


def test_research_uses_web_search_and_the_sites_group_pages(monkeypatch):
    monkeypatch.setattr(subsidiaries, "site_text", lambda url, crawler=None: (f"text of {url}", []))
    home = ('<a href="/az/sirketlerimiz">Şirkətlərimiz</a><a href="/contact">Əlaqə</a>'
            '<a href="https://other.az/group">Başqa sayt</a><a href="/about-us">About</a>')
    ai = FakeAI(FOUND)
    out = subsidiaries.research("Sahil Holding", "https://sahil.az", client=ai,
                                crawler=FakeSite({"https://sahil.az": home}))
    assert [f["name"] for f in out] == ["Sahil Logistika MMC", "Sahil Aqro MMC"]
    call = ai.calls[0]
    assert call["tools"] == [{"type": "web_search"}] and call["text"]["format"]["type"] == "json_schema"
    assert call["model"] == "gpt-5-mini" and call["reasoning"] == {"effort": "low"}  # dərin axtarış, ~15 san.
    assert "Sahil Holding şirkətləri" in call["input"] and "subsidiaries" in call["input"]  # 3 dildə axtarış
    assert "text of https://sahil.az/az/sirketlerimiz" in call["input"] and "text of https://sahil.az/about-us" in call["input"]
    assert "other.az" not in call["input"] and "/contact" not in call["input"]


def test_save_links_to_parent_without_duplicates_or_stealing(factory):  # noqa: F811
    with factory() as s:
        parent = companies.company_for(s, "Sahil Holding", "sahil.az")
        other = companies.company_for(s, "Başqa Holding", None)
        taken = companies.company_for(s, "Sahil Aqro MMC", None)
        taken.parent_id = other.id
        s.commit()
        found = subsidiaries._clean(FOUND + [{"name": "Sahil Holding", "website": "sahil.az", "relation": "",
                                              "source_url": "https://a.az", "in_azerbaijan": True}], "Sahil")
        subs = subsidiaries.save(s, parent, found)
        assert [o.name for o in subs] == ["Sahil Logistika MMC"]  # Aqro başqa ana şirkətə bağlıdır, özü-özünə bağlanmır
        assert subs[0].parent_source == "https://sahil.az/sirketler" and subs[0].listed_at is not None
        assert parent.subsidiaries_checked_at is not None and parent.parent_id is None
        subsidiaries.save(s, parent, found)  # təkrar — yeni sətir yaranmır
        assert len(list(s.scalars(select(Organization).where(Organization.name == "Sahil Logistika MMC")))) == 1


def test_analysis_page_researches_then_analyses_each_subsidiary(factory, fake_ai, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OPENAI_API_KEY", "t")
    calls = []

    def fake_research(name, site="", **kw):
        calls.append(name)
        return subsidiaries._clean(FOUND, name)
    monkeypatch.setattr(subsidiaries, "research", fake_research)
    client = TestClient(web.app)

    page = client.post("/tools/company", data={"name": "Sahil Holding", "website": "sahil.az"}).text
    assert 'id="subsidiaries"' in page and _auto(page)  # analiz səhifəsində araşdırma özü başlayır
    with factory() as s:
        parent = s.scalars(select(Organization).where(Organization.name == "Sahil Holding")).one()

    data = client.post(f"/tools/company/{parent.id}/subsidiaries").json()
    assert data["count"] == 2 and "Sahil Logistika MMC" in data["html"] and "data-sub-pending" in data["html"]
    assert client.post(f"/tools/company/{parent.id}/subsidiaries").json()["count"] == 2 and calls == ["Sahil Holding"]

    with factory() as s:
        sub = s.scalars(select(Organization).where(Organization.name == "Sahil Logistika MMC")).one()
    card = client.post(f"/tools/company/subsidiary/{sub.id}/analyse").json()["html"]
    assert "data-sub-pending" not in card and "Yüksək" in card and "Sahil logistika şirkətidir." in card
    with factory() as s:
        assert s.get(Organization, sub.id).analysed_at is not None

    sub_page = client.get(f"/companies/{sub.id}").text
    assert "Ana şirkət:" in sub_page and f"/companies/{parent.id}" in sub_page
    parent_page = client.get(f"/companies/{parent.id}").text
    assert "Sahil Aqro MMC" in parent_page and not _auto(parent_page)  # kartda yalnız düymə ilə


def test_card_page_does_not_start_paid_research(factory, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OPENAI_API_KEY", "t")
    with factory() as s:
        org = companies.company_for(s, "Sahil Holding", None)
        s.commit()
    page = TestClient(web.app).get(f"/companies/{org.id}").text
    assert "Alt şirkətləri araşdır" in page and not _auto(page)


def test_research_failure_is_a_clear_error(factory, monkeypatch):  # noqa: F811
    def boom(name, site="", **kw):
        raise subsidiaries.OutreachError("Alt şirkətlər araşdırılmadı: Timeout")
    monkeypatch.setattr(subsidiaries, "research", boom)
    with factory() as s:
        org = companies.company_for(s, "Sahil Holding", None)
        s.commit()
    r = TestClient(web.app).post(f"/tools/company/{org.id}/subsidiaries")
    assert r.status_code == 502 and "Timeout" in r.json()["error"]
    r = TestClient(web.app).post(f"/tools/company/subsidiary/{org.id}/analyse")
    assert r.status_code == 404  # alt şirkət deyil


def test_parent_domain_page_is_not_taken_as_the_subsidiary_site(factory):  # noqa: F811
    """AI alt şirkətin "saytı" kimi ana şirkətin səhifəsini verəndə alt şirkətlər ana şirkətə qarışmamalıdır."""
    found = [{"name": f"Azersun {n} Fabriki", "website": "https://www.azersun.com/en/activity-fields/qida",
              "relation": "törəmə şirkət", "source_url": "https://www.azersun.com/en/activity-fields/qida",
              "in_azerbaijan": True} for n in ("Çay", "Yağ")]
    clean = subsidiaries._clean(found, "Azersun Holding", "https://www.azersun.com")
    assert [f["website"] for f in clean] == [None, None]
    with factory() as s:
        parent = companies.company_for(s, "Azersun Holding", "azersun.com")
        s.commit()
        subs = subsidiaries.save(s, parent, subsidiaries._clean(found, "Azersun Holding"))  # sayt süzülməsə də
        assert sorted(o.name for o in subs) == ["Azersun Yağ Fabriki", "Azersun Çay Fabriki"]
        assert all(o.domain is None for o in subs)
