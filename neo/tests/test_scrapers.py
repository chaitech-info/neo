from bs4 import BeautifulSoup

from app.scrapers import EmlakScraper, News1Scraper, OxuScraper


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def test_1news_takes_all_title_blocks_not_only_desc():
    html = """
    <div class="desc"><h3 class="title"><a href="/az/news/20260923100000000-Top">Top xəbər</a></h3></div>
    <div class="news"><h3 class="title"><a href="/az/news/20260923110000000-Lent">Lentdəki xəbər</a></h3></div>
    <h3 class="title"><a href="/az/category/xronika">Xronika</a></h3>
    """
    urls = [u for u, _ in News1Scraper().article_links(_soup(html))]

    assert urls == [
        "https://1news.az/az/news/20260923100000000-Top",
        "https://1news.az/az/news/20260923110000000-Lent",
    ]


def test_oxu_takes_feed_links_without_flash_class_and_skips_tags():
    html = """
    <a class="umami--click--flash" href="/cemiyyet/flash-bolmesindeki-xeber">Flash bölməsindəki xəbər başlığı</a>
    <a class="feed-slider-item-img" href="/iqtisadiyyat/lentdeki-korporativ-tedbir"></a>
    <a href="/iqtisadiyyat/lentdeki-korporativ-tedbir">Lentdəki korporativ tədbir başlığı</a>
    <a href="https://oxu.az/tag/bmt-bas-assambleyasi">BMT Baş Assambleyası etiketi</a>
    """
    links = OxuScraper().article_links(_soup(html))

    assert links == [
        ("https://oxu.az/cemiyyet/flash-bolmesindeki-xeber", "Flash bölməsindəki xəbər başlığı"),
        ("https://oxu.az/iqtisadiyyat/lentdeki-korporativ-tedbir", "Lentdəki korporativ tədbir başlığı"),
    ]


def test_emlak_resolves_relative_and_unicode_links():
    html = """
    <a href="https://emlak.gov.az/az/news/view/10524-ozellesdirme">İnvestisiya müsabiqəsi elanı</a>
    <a href="/az/news/view/10540-novbeti-herracda-6-neqliyyat">Növbəti hərracda 6 nəqliyyat vasitəsi satılıb</a>
    <a href="az/news/view/10202-Əmlak-Xidməti">Yeni kommersiya obyekti icarəyə verilir</a>
    <a href="/az/news/view/4856-Əlaqə">Əlaqə</a>
    """
    scraper = EmlakScraper()
    scraper.get = lambda url: FakeResponse(html)
    scraper.polite_sleep = lambda: None

    urls = sorted(lead.source_url for lead in scraper.fetch_leads())

    assert urls == [
        "https://emlak.gov.az/az/news/view/10202-Əmlak-Xidməti",
        "https://emlak.gov.az/az/news/view/10524-ozellesdirme",
        "https://emlak.gov.az/az/news/view/10540-novbeti-herracda-6-neqliyyat",
    ]


def test_keyword_filter_matches_azerbaijani_uppercase_titles():
    from app.scrapers.base import az_lower

    assert "tədbir" in az_lower("Restoranla bağlı TƏDBİR GÖRÜLƏCƏK")
    assert "açılış" in az_lower("Yeni ofisin AÇILIŞI oldu")
    assert "ictimai iaşə" in az_lower("İCTİMAİ İAŞƏ xidməti")


ARTICLE_HTML = """
<html><head>
<meta property="article:published_time" content="2026-09-23T20:56:00+04:00">
<meta property="og:description" content="Qısa təsvir">
</head><body>
<div class="sidebar"><p>Reklam</p></div>
<div class="post-detail-content-inner"><p>Birinci abzas.</p><p>200 nəfərlik banket.</p></div>
</body></html>
"""


def _scraper_with_page(html: str) -> OxuScraper:
    scraper = OxuScraper()
    scraper.get = lambda url: FakeResponse(html)
    scraper.polite_sleep = lambda: None
    return scraper


def test_fetch_article_extracts_body_and_utc_publish_time():
    text, posted_at = _scraper_with_page(ARTICLE_HTML).fetch_article("https://oxu.az/x/y")

    assert text == "Birinci abzas.\n200 nəfərlik banket."
    assert posted_at.isoformat() == "2026-09-23T16:56:00"


def test_fetch_article_falls_back_when_selector_missing():
    html = ARTICLE_HTML.replace("post-detail-content-inner", "renamed-by-site")
    text, _ = _scraper_with_page(html).fetch_article("https://oxu.az/x/y")

    assert "200 nəfərlik banket." in text


def test_enrich_keeps_title_when_article_fetch_fails():
    from app.models import RawLead

    scraper = OxuScraper()
    scraper.polite_sleep = lambda: None

    def boom(url):
        raise RuntimeError("down")

    scraper.get = boom
    lead = RawLead(source="oxu", source_url="https://oxu.az/x/y", title="Başlıq", raw_text="Başlıq")

    assert scraper.enrich(lead).raw_text == "Başlıq"
    assert lead.posted_at is None


FED_TENDERS_HTML = """
<div class="news"><a href="/az/tenderler/dovlet-qurumu-keytring-xidmeti-alir-tender-elani-264949">
  <div class="news-detail"><span class="category-name">Tenderlər</span><span class="time date">21 Sen 2026</span></div>
  <div class="heading"><h5>Dövlət qurumu keytring xidməti alır – TENDER ELANI</h5></div></a></div>
<div class="news"><a href="/az/tenderler/44-mekteb-tikilecek-25-milyonluq-tender-264950">
  <div class="heading"><h5>44 məktəb tikiləcək – 25 MİLYONLUQ TENDER</h5></div></a></div>
<div class="news"><a href="/az/maliyye/bank-xeberi-265000"><div class="heading"><h5>Bank tədbiri</h5></div></a></div>
<a href="az/tenderler/2">2</a>
"""


def test_fed_tenders_reads_cards_filters_keywords_and_paginates():
    from app.models import LeadCategory
    from app.scrapers import FedTenderScraper

    scraper = FedTenderScraper()
    requested = []
    scraper.get = lambda url: requested.append(url) or FakeResponse(FED_TENDERS_HTML)
    scraper.polite_sleep = lambda: None

    leads = scraper.fetch_leads()

    assert requested[:2] == ["https://fed.az/az/tenderler", "https://fed.az/az/tenderler/2"]
    assert [lead.title for lead in leads] == ["Dövlət qurumu keytring xidməti alır – TENDER ELANI"]
    assert leads[0].source.value == "fed"
    assert leads[0].category == LeadCategory.tender


def test_azerweb_skips_expired_tenders():
    from datetime import date

    from app.scrapers import AzerwebScraper

    payload = {"results": [
        {"id": 1, "type": "tender", "title": "Catering 2019", "company": "GIZ", "deadline": "2019-11-15", "posted_date": "2019-11-07"},
        {"id": 2, "type": "tender", "title": "Catering açıq", "company": "FAO", "deadline": "2026-10-01", "posted_date": "2026-09-20"},
        {"id": 3, "type": "tender", "title": "Deadline-sız", "company": "X", "deadline": None, "posted_date": None},
    ]}
    scraper = AzerwebScraper()
    scraper.today = lambda: date(2026, 9, 23)
    scraper.get = lambda url, **kw: type("R", (), {"json": lambda self: payload})()
    scraper.polite_sleep = lambda: None

    leads = scraper.fetch_leads()

    assert [lead.title for lead in leads] == ["Catering açıq", "Deadline-sız"]
    assert leads[0].posted_at.isoformat() == "2026-09-19T20:00:00"


def test_fed_keeps_clean_title_when_card_text_has_category_and_date():
    from bs4 import BeautifulSoup

    from app.scrapers import FedScraper

    href = "/az/senaye/azersun-zavodu-boyudur-265418"
    html = f"""
    <a href="{href}">Sənaye26 Sen 2026“Azərsun” zavodunu böyüdür</a>
    <a href="{href}">“Azərsun” zavodunu böyüdür</a>
    <a href="{href}">“Azərsun” zavodunu böyüdür19:3626 Sen 2026</a>
    """
    links = FedScraper().article_links(BeautifulSoup(html, "lxml"))

    assert links == [(f"https://fed.az{href}", "“Azərsun” zavodunu böyüdür")]


def test_fed_strips_glued_category_date_and_time_date():
    from bs4 import BeautifulSoup

    from app.scrapers import FedScraper

    html = """
    <a href="/az/senaye/mingecevir-tekstil-265403">Sənaye26 Sen 2026Mingəçevirdə tekstil layihəsinin təməli qoyuldu</a>
    <a href="/az/senaye/mingecevir-tekstil-265403">Mingəçevirdə tekstil layihəsinin təməli qoyuldu13:0126 Sen 2026</a>
    """
    links = FedScraper().article_links(BeautifulSoup(html, "lxml"))

    assert links == [("https://fed.az/az/senaye/mingecevir-tekstil-265403",
                      "Mingəçevirdə tekstil layihəsinin təməli qoyuldu")]


def test_report_senaye_reads_title_element_only():
    from bs4 import BeautifulSoup

    from app.scrapers import ReportSenayeScraper

    html = """
    <a class="news__item" href="/senaye/azergold-yeni-meden">
      <span class="news__category">Sənaye</span>
      <h2 class="news__title">"AzerGold" yeni qızıl mədənində istehsala başlayır</h2>
      <ul class="news__date"><li>25 sentyabr, 2026</li><li>19:05</li></ul>
    </a>
    <a class="news__item" href="/iqtisadiyyat/other">Başqa bölmə</a>
    """
    links = ReportSenayeScraper().article_links(BeautifulSoup(html, "lxml"))

    assert links == [("https://report.az/senaye/azergold-yeni-meden",
                      '"AzerGold" yeni qızıl mədənində istehsala başlayır')]


def test_news_keywords_skip_state_events_and_match_expansion():
    from app.scrapers.base import NEWS_KEYWORDS, contains_keyword

    assert not contains_keyword("II Azərbaycan Beynəlxalq İnvestisiya Forumunda sənədlər imzalandı", NEWS_KEYWORDS)
    assert not contains_keyword("Mədəniyyət nazirliyi yeni layihə təqdim edib", NEWS_KEYWORDS)
    assert contains_keyword("“Azərsun” mövcud zavodunu iki dəfə böyütməyi planlaşdırır", NEWS_KEYWORDS)
    assert contains_keyword("Filizçay polimetal yatağında ilk istehsalın başlayacağı vaxt açıqlanıb", NEWS_KEYWORDS)
