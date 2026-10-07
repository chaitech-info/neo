from bs4 import BeautifulSoup

from app.models import LeadCategory
from app.scrapers.media import MediaScraper, MediaSite, clean_title

LISTING = """
<div>
  <a class="item" href="https://apa.az/senaye-ve-energetika/yeni-zavod-ucun-iscilere-nahar-verilecek-1">
    <p class="lent-title">Yeni zavodda işçilərə nahar veriləcək</p><span>09:16</span> 7 oktyabr 2026</a>
  <a href="https://apa.az/senaye-ve-energetika/neft-hasilati-proqnozu-artirilib-1002684">Neft hasilatı proqnozu artırılıb</a>
  <a href="https://apa.az/idman/futbol-oyunu-neticesi-1002685">Futbol oyunu nəticəsi açıqlandı</a>
  <a href="/senaye-ve-energetika/nisbi-link-yeni-sənaye-parki-acilir-9">Nisbi link: yeni sənaye parkı açılır</a>
  <a href="https://apa.az/senaye-ve-energetika/yeni-zavod-ucun-iscilere-nahar-verilecek-1"><img alt="x" src="a.jpg"></a>
  <a href="https://apa.az/senaye-ve-energetika/sekil-linki-basliq-alt-dadir-123"><img alt="Şəkil linkinin başlığı alt-dadır" src="a.jpg"></a>
</div>
"""
SITE = MediaSite("apa", "APA.az", "https://apa.az/az", r"^https://apa\.az/senaye-ve-energetika/[a-z0-9-]{15,}$")


class FakeResponse:
    def __init__(self, text):
        self.text = text


def _scraper(sites):
    scraper = MediaScraper()
    scraper.sites = sites
    scraper.polite_sleep = lambda: None
    scraper.get = lambda url: FakeResponse(LISTING)
    scraper.enrich = lambda lead: lead
    return scraper


def test_clean_title_strips_dates_times_and_trend_suffix():
    assert clean_title("Başlıq mətni 09:16 7 oktyabr 2026") == "Başlıq mətni"
    assert clean_title("29-09-2026 | Azərbaycan sərgidə iştirak edir") == "Azərbaycan sərgidə iştirak edir"
    assert clean_title("EIA raises Brent forecast (UTC+04) Economy News") == "EIA raises Brent forecast"
    assert clean_title("29 September, 2026 AFEZ news title here") == "AFEZ news title here"


def test_article_links_filter_by_pattern_dedupe_and_use_img_alt():
    links = dict(_scraper([SITE]).article_links(SITE, BeautifulSoup(LISTING, "lxml")))
    assert links == {
        "https://apa.az/senaye-ve-energetika/yeni-zavod-ucun-iscilere-nahar-verilecek-1": "Yeni zavodda işçilərə nahar veriləcək",
        "https://apa.az/senaye-ve-energetika/neft-hasilati-proqnozu-artirilib-1002684": "Neft hasilatı proqnozu artırılıb",
        "https://apa.az/senaye-ve-energetika/sekil-linki-basliq-alt-dadir-123": "Şəkil linkinin başlığı alt-dadır",
    }


def test_only_keyword_matches_become_leads_with_site_category():
    site = MediaSite(**{**SITE.__dict__, "category": LeadCategory.event})
    leads = _scraper([site]).fetch_leads()
    assert [lead.source_url for lead in leads] == [
        "https://apa.az/senaye-ve-energetika/yeni-zavod-ucun-iscilere-nahar-verilecek-1"]
    assert leads[0].source == "media"
    assert leads[0].category == LeadCategory.event
    assert "Mənbə: APA.az" in leads[0].raw_text


def test_failing_and_empty_sites_do_not_stop_others():
    broken = MediaSite("b", "Qırıq", "https://broken.az/", r"^https://broken\.az/x$")
    empty = MediaSite("e", "Boş", "https://empty.az/", r"^https://empty\.az/zzz$")
    scraper = _scraper([broken, empty, SITE])
    real_get = scraper.get
    scraper.get = lambda url: (_ for _ in ()).throw(RuntimeError("down")) if "broken" in url else real_get(url)
    assert len(scraper.fetch_leads()) == 1


def test_must_match_gate_drops_unrelated_global_news_before_triage():
    from app.scrapers.media import AZ_GATE

    html = """
<a href="https://acme.com/news/a-solar-plant-in-chile-opens-today-1">Solar plant in Chile opens today with 500 workers</a>
<a href="https://acme.com/news/acme-signs-baku-wind-farm-deal-2">Acme signs Baku wind farm deal, new plant opens</a>
<a href="https://acme.com/news/absheron-gas-project-update-3">Gas project update for the region this quarter</a>
"""
    site = MediaSite("acme", "Acme", "https://acme.com/news", r"^https://acme\.com/news/[a-z0-9-]{15,}$", must_match=AZ_GATE)
    scraper = _scraper([site])
    scraper.get = lambda url: FakeResponse(html)
    seen = []
    real = scraper.select_relevant
    scraper.select_relevant = lambda texts, kw, kind: (seen.extend(texts), [True] * len(texts))[1]
    leads = scraper.fetch_leads()

    assert sorted(l.source_url.rsplit("/", 1)[-1] for l in leads) == [
        "absheron-gas-project-update-3", "acme-signs-baku-wind-farm-deal-2"]   # Chile atıldı (nə başlıqda, nə URL-də)
    assert all("Chile" not in t for t in seen)


def test_new_company_sites_patterns_match_real_urls():
    import re

    from app.scrapers.media import MEDIA_SITES

    by_key = {s.key: s for s in MEDIA_SITES}
    ok = {
        "socar_press": "https://www.socar.az/az/post/nis-seherinde-kombineedilmis-buxar-qaz-elektrik-stansiyasinin-insasi",
        "acwa": "https://acwapower.com/en/media-center/latest-news/acwa-led-consortium-reaches-commercial-operation-at-the-red-sea/",
    }
    for key, url in ok.items():
        assert re.search(by_key[key].href_pattern, url), key
    assert clean_title("01/10/2026 SOCAR yeni zavod açır") == "SOCAR yeni zavod açır"
    assert clean_title("10/26 2021 What is a PPA? Read the article") == "What is a PPA?"
