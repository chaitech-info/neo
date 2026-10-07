from datetime import datetime, timedelta

from app.scrapers import EtenderConstructionScraper, EtenderScraper
from app.scrapers.base import CONSTRUCTION_KEYWORDS, contains_keyword
from app.tenders import reference_for

URL = "https://etender.gov.az/main/competition/detail/123456"


def test_construction_keywords_match_big_works_not_small_repairs():
    for title in ("Sənaye parkında zavod binasının inşası", "Yol tikintisi və körpü işləri",
                  "Əsaslı təmir işlərinin satın alınması", "Construction of a warehouse complex",
                  "Məktəbin yenidənqurma işləri"):
        assert contains_keyword(title, CONSTRUCTION_KEYWORDS), title
    for title in ("Cari təmir işləri", "Kompüter avadanlığının alınması", "Dəftərxana ləvazimatı", "Banket xidməti"):
        assert not contains_keyword(title, CONSTRUCTION_KEYWORDS), title


def test_same_etender_tender_gets_same_reference_from_both_sources():
    assert reference_for("etender", URL) == "etender:123456"
    assert reference_for("etender_construction", URL) == "etender:123456"   # dublikat tutulsun


def _api(titles):
    end = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
    items = [{"eventId": i, "eventName": t, "buyerOrganizationName": "Sifarişçi", "endDate": end,
              "publishDate": "2026-10-05T10:00:00"} for i, t in enumerate(titles, 1)]
    return type("R", (), {"json": lambda self: {"items": items, "hasNextPage": False}})()


def _run(cls, titles):
    scraper = cls()
    scraper.get = lambda url, **kw: _api(titles)
    scraper.polite_sleep = lambda: None
    return scraper.fetch_leads()


def test_construction_scraper_filters_by_construction_words_and_uses_own_source_key():
    titles = ["Zavod binasının tikintisi", "Banket xidməti", "Kompüter alınması"]
    construction = _run(EtenderConstructionScraper, titles)
    meals = _run(EtenderScraper, titles)

    assert [(l.title, l.source) for l in construction] == [("Zavod binasının tikintisi", "etender_construction")]
    assert [(l.title, l.source) for l in meals] == [("Banket xidməti", "etender")]   # əvvəlki davranış dəyişməyib
    assert construction[0].source_url == "https://etender.gov.az/main/competition/detail/1"


def test_construction_ignores_buyer_name_and_excludes_paperwork_and_equipment():
    end = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")

    def item(i, name, buyer="Sifarişçi"):
        return {"eventId": i, "eventName": name, "buyerOrganizationName": buyer, "endDate": end, "publishDate": "2026-10-05T10:00:00"}

    items = [
        item(1, "Zavod binasının tikintisi"),
        item(2, "Aktivlərin idarəetmə sisteminin alınması", buyer="Azərbaycan Dəmir Yolları"),   # sifarişçi adı sayılmır
        item(3, "Qəsəbənin tikintisi üçün layihə-smeta sənədlərinin hazırlanması"),                # kağız işi
        item(4, "Tikinti avadanlıqlarının satın alınması"),                                          # avadanlıq
        item(5, "Məktəbin əsaslı təmiri"),
    ]
    scraper = EtenderConstructionScraper()
    scraper.get = lambda url, **kw: type("R", (), {"json": lambda self: {"items": items, "hasNextPage": False}})()
    scraper.polite_sleep = lambda: None
    assert [l.title for l in scraper.fetch_leads()] == ["Zavod binasının tikintisi", "Məktəbin əsaslı təmiri"]
