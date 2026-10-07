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
    assert leads[0].source.value == "media"
    assert leads[0].category == LeadCategory.event
    assert "Mənbə: APA.az" in leads[0].raw_text


def test_failing_and_empty_sites_do_not_stop_others():
    broken = MediaSite("b", "Qırıq", "https://broken.az/", r"^https://broken\.az/x$")
    empty = MediaSite("e", "Boş", "https://empty.az/", r"^https://empty\.az/zzz$")
    scraper = _scraper([broken, empty, SITE])
    real_get = scraper.get
    scraper.get = lambda url: (_ for _ in ()).throw(RuntimeError("down")) if "broken" in url else real_get(url)
    assert len(scraper.fetch_leads()) == 1
